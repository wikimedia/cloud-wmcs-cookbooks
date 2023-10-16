r"""WMCS Ceph - Undrain all the osd damons from a host

Usage example:
    cookbook wmcs.ceph.reboot_node \
        --hostname cloudcephosd2001-dev

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.ceph import CephClusterController, get_node_cluster_name
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts

LOGGER = logging.getLogger(__name__)


class UndrainNode(CookbookBase):
    """WMCS Ceph cookbook to undrain a ceph OSD node."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--node",
            required=True,
            action="append",
            help="Hostname (no subdomain) of the node to drain. Pass more than once to drain multiple nodes.",
        )
        parser.add_argument(
            "--set-maintenance",
            required=False,
            default=False,
            action="store_true",
            help="If passed, it will set the cluster in maintenance mode (note tht it will not rebalance any data)",
        )
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="If passed, will continue even if the cluster is not in a healthy state.",
        )
        parser.add_argument(
            "--wait",
            required=False,
            action="store_true",
            help=(
                "If passed, will wait until the cluster finishes rebalancing (note that if it does "
                "not have to rebalance, might wait forever for the rebalancing to start)."
            ),
        )
        parser.add_argument(
            "--batch-size",
            required=False,
            type=int,
            help="Amount of osd daemons to undrain at a time.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, UndrainNodeRunner,)(
            hosts_to_undrain=args.node,
            set_maintenance=args.set_maintenance,
            force=args.force,
            wait=args.wait,
            spicerack=self.spicerack,
            batch_size=args.batch_size,
        )


class UndrainNodeRunner(WMCSCookbookRunnerBase):
    """Runner for UndrainNode"""

    def __init__(
        self,
        common_opts: CommonOpts,
        hosts_to_undrain: list[str],
        force: bool,
        wait: bool,
        set_maintenance: bool,
        batch_size: int,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        self.common_opts = common_opts
        self.hosts_to_undrain = hosts_to_undrain
        self.set_maintenance = set_maintenance
        self.force = force
        self.wait = wait
        self.batch_size = batch_size
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(),
            cluster_name=get_node_cluster_name(node=self.hosts_to_undrain[0]),
            spicerack=self.spicerack,
        )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        LOGGER.info("Undraining nodes %s", self.hosts_to_undrain)

        if not self.force:
            self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True)

        if self.set_maintenance:
            silences = self.controller.set_maintenance(
                task_id=self.common_opts.task_id,
                reason=f"Undraining node {self.hosts_to_undrain}",
            )
        else:
            silences = []

        for node in self.hosts_to_undrain:
            self.controller.undrain_osd_node(osd_host=node, wait=self.wait, batch_size=self.batch_size)

        if self.force:
            LOGGER.info("Force passed, ignoring cluster health and continuing")
        else:
            LOGGER.info(
                "Undrained node %s, waiting for cluster to stabilize...",
                self.hosts_to_undrain,
            )
            self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True)
            LOGGER.info("Cluster healthy, continuing")

        if self.set_maintenance:
            self.controller.unset_maintenance(silences=silences)

        LOGGER.info("Finished undraining node %s", self.hosts_to_undrain)
