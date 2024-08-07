r"""WMCS Ceph - Undrain all the osd nodes from a rack

Usage example:
    cookbook wmcs.ceph.undrain_rack \
        --rack D5

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from cookbooks.wmcs.ceph.osd.undrain_node import UndrainNodeRunner
from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

LOGGER = logging.getLogger(__name__)


class UndrainRack(CookbookBase):
    """WMCS Ceph cookbook to undrain all the nodes from a rack.

    This is done in a very gentle way, undraining small batches of osds and waiting for rebalance after each
    batch before continuing with the next.
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
                "Rack to undrain the nodes of (ex. D5, E4, see `ceph osd tree` for the exact names). NOTE: undraining "
                "more than one rack might break the cluster availability."
            ),
        )
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to undrain the rack of.",
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
                "If passed, it will not wait for the cluster to finish rebalancing (note that if it does "
                "not have to rebalance, might wait forever for the rebalancing to start)."
            ),
        )
        parser.add_argument(
            "--osd-id",
            required=False,
            action="append",
            type=int,
            help=(
                "If passed, will only undrain the given OSD daemon ids. Use multiple times to destroy more than one "
                "osd."
            ),
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            UndrainRackRunner,
        )(
            rack_to_undrain=args.rack,
            set_maintenance=args.set_maintenance,
            cluster_name=args.cluster_name,
            osd_ids=args.osd_ids,
            force=args.force,
            wait=not args.no_wait,
            spicerack=self.spicerack,
        )


class UndrainRackRunner(WMCSCookbookRunnerBase):
    """Runner for UndrainRack"""

    def __init__(
        self,
        common_opts: CommonOpts,
        rack_to_undrain: str,
        force: bool,
        wait: bool,
        cluster_name: CephClusterName,
        osd_ids: list[int],
        set_maintenance: bool,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        self.common_opts = common_opts
        self.rack_to_undrain = rack_to_undrain
        self.set_maintenance = set_maintenance
        self.osd_ids = osd_ids
        self.cluster_name = cluster_name
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
        LOGGER.info("Undraining all the nodes for rack %s", self.rack_to_undrain)

        racks = self.controller.get_osd_tree().get_nodes_by_type(wanted_type="rack")
        rack = next(
            (rack for rack in racks if rack.name == self.rack_to_undrain),
            None,
        )
        if rack is None:
            raise Exception(f"Unable to find rack {self.rack_to_undrain}, got {[rack.name for rack in racks]}")

        # If we ever change the tree, and have another level this will have to change
        hosts = [child.name for child in rack.children]
        undrain_node_cookbook = UndrainNodeRunner(
            common_opts=self.common_opts,
            osd_hostnames=hosts,
            force=self.force,
            set_maintenance=self.set_maintenance,
            spicerack=self.spicerack,
            wait=self.wait,
            cluster_name=self.cluster_name,
            batch_size=0,
            osd_ids=self.osd_ids,
        )
        undrain_node_cookbook.run()

        LOGGER.info("Finished draining rack %s", self.rack_to_undrain)
