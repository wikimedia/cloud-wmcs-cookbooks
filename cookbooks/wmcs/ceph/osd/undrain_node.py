r"""WMCS Ceph - Undrain all the osd damons from a host

Usage example:
    cookbook wmcs.ceph.reboot_node \
        --osd-hostname cloudcephosd2001-dev \
        --cluster-name codfw1

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

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
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to roll restart.",
        )
        parser.add_argument(
            "--osd-hostname",
            required=True,
            action="append",
            help=(
                "Hostname of the new OSDs to add. Repeat for each new OSD. If specifying more "
                "than one, consider passing --yes-i-know-what-im-doing"
            ),
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
            "--no-wait",
            required=False,
            action="store_true",
            help=(
                "If not passed, will not wait until the cluster finishes rebalancing (note that if it does "
                "not have to rebalance, might wait forever for the rebalancing to start)."
            ),
        )
        parser.add_argument(
            "--batch-size",
            required=False,
            default=0,
            type=int,
            help="Amount of osd daemons to undrain at a time (0 for no batches).",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            UndrainNodeRunner,
        )(
            osd_hostnames=args.osd_hostname,
            cluster_name=args.cluster_name,
            set_maintenance=args.set_maintenance,
            force=args.force,
            wait=not args.no_wait,
            spicerack=self.spicerack,
            batch_size=args.batch_size,
        )


class UndrainNodeRunner(WMCSCookbookRunnerBase):
    """Runner for UndrainNode"""

    def __init__(
        self,
        common_opts: CommonOpts,
        osd_hostnames: list[str],
        cluster_name: CephClusterName,
        force: bool,
        wait: bool,
        set_maintenance: bool,
        batch_size: int,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        self.common_opts = common_opts
        self.osd_fqdns = [
            hostname.split(".", 1)[0] + f".{cluster_name.get_site().get_domain()}" for hostname in osd_hostnames
        ]
        self.set_maintenance = set_maintenance
        self.cluster_name = cluster_name
        self.force = force
        self.wait = wait
        self.batch_size = batch_size
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(), cluster_name=cluster_name, spicerack=self.spicerack
        )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        LOGGER.info("Undraining nodes %s", self.osd_fqdns)

        if not self.force:
            self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True)

        if self.set_maintenance:
            silences = self.controller.set_maintenance(
                task_id=self.common_opts.task_id,
                reason=f"Undraining node {self.osd_fqdns}",
            )
        else:
            silences = []

        for node in self.osd_fqdns:
            self.controller.undrain_osd_node(osd_fqdn=node, wait=self.wait, batch_size=self.batch_size)

        if self.force:
            LOGGER.info("Force passed, ignoring cluster health and continuing")
        else:
            LOGGER.info(
                "Undrained node %s, waiting for cluster to stabilize...",
                self.osd_fqdns,
            )
            self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True)
            LOGGER.info("Cluster healthy, continuing")

        if self.set_maintenance:
            self.controller.unset_maintenance(silences=silences)

        LOGGER.info("Finished undraining node %s", self.osd_fqdns)
