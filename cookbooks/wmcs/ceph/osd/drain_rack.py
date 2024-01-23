r"""WMCS Ceph - Drain all the osd nodes from a rack

Usage example:
    cookbook wmcs.ceph.drain_rack \
        --rack D5

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from cookbooks.wmcs.ceph.osd.drain_node import DrainNodeRunner
from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

LOGGER = logging.getLogger(__name__)


class DrainRack(CookbookBase):
    """WMCS Ceph cookbook to drain all the nodes from a rack.

    This is done in a very gentle way, draining small batches of osds and waiting for rebalance after each batch
    before continuing with the next.
    """

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
            "--rack",
            required=True,
            help=(
                "Rack to drain the nodes of (ex. D5, E4, see `ceph osd tree` for the exact names). NOTE: draining "
                "more than one rack might break the cluster availability."
            ),
        )
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to drain the rack of.",
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
        return with_common_opts(self.spicerack, args, DrainRackRunner,)(
            rack_to_drain=args.rack,
            set_maintenance=args.set_maintenance,
            cluster_name=args.cluster_name,
            force=args.force,
            wait=not args.no_wait,
            spicerack=self.spicerack,
        )


class DrainRackRunner(WMCSCookbookRunnerBase):
    """Runner for DrainRack"""

    def __init__(
        self,
        common_opts: CommonOpts,
        rack_to_drain: str,
        force: bool,
        wait: bool,
        cluster_name: CephClusterName,
        set_maintenance: bool,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        self.common_opts = common_opts
        self.rack_to_drain = rack_to_drain
        self.set_maintenance = set_maintenance
        self.force = force
        self.wait = wait
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(),
            cluster_name=cluster_name,
            spicerack=self.spicerack,
        )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        LOGGER.info("Draining all the nodes for rack %s", self.rack_to_drain)

        racks = self.controller.get_osd_tree().get_nodes_by_type(wanted_type="rack")
        rack = next(
            (rack for rack in racks if rack.name == self.rack_to_drain),
            None,
        )
        if rack is None:
            raise Exception(f"Unable to find rack {self.rack_to_drain}, got {[rack.name for rack in racks]}")

        # If we ever change the tree, and have another level this will have to change
        hosts = [child.name for child in rack.children]
        drain_node_cookbook = DrainNodeRunner(
            common_opts=self.common_opts,
            hosts_to_drain=hosts,
            force=self.force,
            set_maintenance=self.set_maintenance,
            spicerack=self.spicerack,
            wait=self.wait,
        )
        drain_node_cookbook.run()

        LOGGER.info("Finished draining rack %s", self.rack_to_drain)
