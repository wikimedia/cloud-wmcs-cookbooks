r"""WMCS Ceph - Reset all the weights, the crush weight to the size in TB, and reweight to 1

Usage example:
    cookbook wmcs.ceph.reset_weights
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


class ResetWeight(CookbookBase):
    """WMCS Ceph cookbook to reset all the weights for all osds, usually used if you had to manually mess around when
    draining a rack or similar.
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
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to reset weights for.",
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
            "--rack",
            required=False,
            default="all",
            help="Rack to reset the weight for (ex. D5, E4, see `ceph osd tree` for the exact names).",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            ResetWeightRunner,
        )(
            rack_to_drain=args.rack,
            cluster_name=args.cluster_name,
            force=args.force,
            wait=not args.no_wait,
            spicerack=self.spicerack,
        )


class ResetWeightRunner(WMCSCookbookRunnerBase):
    """Runner for ResetWeight"""

    def __init__(
        self,
        common_opts: CommonOpts,
        rack_to_drain: str,
        force: bool,
        wait: bool,
        cluster_name: CephClusterName,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        self.common_opts = common_opts
        self.rack_to_reset = rack_to_drain
        self.force = force
        self.wait = wait
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(),
            cluster_name=cluster_name,
            spicerack=self.spicerack,
        )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        LOGGER.info("Resetting all the weights for racks: %s on cluster %s", self.rack_to_reset, self.cluster_name)

        if not self.force:
            self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True)

        racks = list(self.controller.get_osd_tree().get_nodes_by_type(wanted_type="rack"))
        if self.rack_to_reset != "all":
            maybe_rack = next(
                (rack for rack in racks if rack.name == self.rack_to_reset),
                None,
            )
            if maybe_rack is None:
                raise Exception(f"Unable to find rack {self.rack_to_reset}, got {[rack.name for rack in racks]}")

            racks = [maybe_rack]
        else:
            LOGGER.info("Selecting all racks %s", ",".join(rack.name for rack in racks))

        for rack_idx, rack in enumerate(racks):
            log_prefix = f"[{rack.name}|{rack_idx + 1} of {len(racks)}]"
            LOGGER.info(
                "%s Reweighting all osds in rack (%d hosts)",
                log_prefix,
                len(rack.children),
            )
            # If we ever change the tree, and have another level this will have to change
            hosts = list(rack.children)
            for host_idx, host in enumerate(hosts):
                LOGGER.info(
                    "%s[%s|%d of %d] Reweighting all osds in host (%d osds)",
                    log_prefix,
                    host.name,
                    host_idx + 1,
                    len(hosts),
                    len(host.children),
                )
                node_fqdn = f"{host.name}.{self.cluster_name.get_site().get_domain()}"
                for osd in host.children:
                    self.controller.crush_reset_weight_osd(osd_id=osd.node_id, node_fqdn=node_fqdn)
                    self.controller.reweight_osd(osd_id=osd.node_id, new_weight=1.0)

        if self.wait:
            self.controller.wait_for_rebalance()

        LOGGER.info("Finished resetting weights for racks: %s", self.rack_to_reset)
