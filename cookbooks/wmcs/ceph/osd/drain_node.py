r"""WMCS Ceph - Drain all the osd damons from a host or set of hosts

Usage example:
    cookbook wmcs.ceph.drain_node \
        --node cloudcephosd2001-dev \
        --node cloudcephosd2002-dev

"""
from __future__ import annotations

import argparse
import datetime
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.alerts import downtime_host, uptime_host
from wmcs_libs.ceph import CephClusterController, get_node_cluster_name
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts

LOGGER = logging.getLogger(__name__)


class DrainNode(CookbookBase):
    """WMCS Ceph cookbook to drain a ceph OSD node."""

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
            help=(
                "If passed, it will set the cluster in maintenance mode (careful! It will not rebalance data so you "
                "might render the cluster unavailable)."
            ),
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
                "If passed, will wait until the cluster finishes rebalancing (note that if it does "
                "not have to rebalance, might wait forever for the rebalancing to start)."
            ),
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, DrainNodeRunner,)(
            hosts_to_drain=args.node,
            set_maintenance=args.set_maintenance,
            force=args.force,
            wait=not args.no_wait,
            spicerack=self.spicerack,
        )


class DrainNodeRunner(WMCSCookbookRunnerBase):
    """Runner for DrainNode"""

    def __init__(
        self,
        common_opts: CommonOpts,
        hosts_to_drain: list[str],
        force: bool,
        wait: bool,
        set_maintenance: bool,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        self.common_opts = common_opts
        self.hosts_to_drain = hosts_to_drain
        self.set_maintenance = set_maintenance
        self.force = force
        self.wait = wait
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(),
            cluster_name=get_node_cluster_name(node=self.hosts_to_drain[0]),
            spicerack=self.spicerack,
        )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        LOGGER.info("Draining nodes %s", self.hosts_to_drain)

        if not self.force:
            self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True)

        if self.set_maintenance:
            cluster_silences = self.controller.set_maintenance(
                task_id=self.common_opts.task_id,
                reason=f"Draining node {self.hosts_to_drain}",
            )
        else:
            cluster_silences = []

        for idx, maybe_host_name in enumerate(self.hosts_to_drain):
            host_name = maybe_host_name.split(".", 1)[0]
            LOGGER.info(
                "[%s] Draining node %s (%d/%d), waiting for cluster to stabilize...",
                datetime.datetime.now(),
                maybe_host_name,
                idx,
                len(self.hosts_to_drain),
            )
            silence_id = downtime_host(
                spicerack=self.spicerack,
                host_name=host_name,
                comment="Draining with wmcs.ceph.drain_node",
                task_id=self.common_opts.task_id,
                # A bit longer than the timeout for the operation
                duration="6h",
            )

            self.controller.drain_osd_node(
                osd_host=host_name,
                be_unsafe=self.force,
                wait=self.wait,
                batch_size=2,
            )

            if self.force:
                LOGGER.info("Force passed, ignoring cluster health and continuing")
            else:
                LOGGER.info(
                    "[%s] Drained node %s (%d/%d), waiting for cluster to stabilize...",
                    datetime.datetime.now(),
                    maybe_host_name,
                    idx,
                    len(self.hosts_to_drain),
                )
                self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True)
                uptime_host(spicerack=self.spicerack, host_name=host_name, silence_id=silence_id)
                LOGGER.info("[%s] Cluster healthy, continuing", datetime.datetime.now())

        if self.set_maintenance:
            self.controller.unset_maintenance(silences=cluster_silences)

        LOGGER.info("Finished draining nodes %s", self.hosts_to_drain)
