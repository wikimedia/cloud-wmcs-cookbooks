r"""WMCS Ceph - Rolling reboot of all the osd nodes.

Usage example:
    cookbook wmcs.ceph.roll_reboot_osds \
        --cluster-name eqiad1

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.ceph.reboot_node import RebootNode
from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

LOGGER = logging.getLogger(__name__)


class RollRebootOsds(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):
        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to roll reboot.",
        )
        parser.add_argument(
            "--resume-with",
            required=False,
            help="If a previous roll failed midway, specify which node to start with",
        )
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="If passed, will continue even if the cluster is not in a healthy state.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_common_opts(self.spicerack, args, RollRebootOsdsRunner)(
            cluster_name=args.cluster_name, force=args.force, spicerack=self.spicerack, resume_with=args.resume_with
        )


class RollRebootOsdsRunner(WMCSCookbookRunnerBase):
    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: CephClusterName,
        force: bool,
        resume_with: str,
        spicerack: Spicerack,
    ):
        self.common_opts = common_opts
        self.force = force
        self.resume_with = resume_with
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(), cluster_name=cluster_name, spicerack=self.spicerack
        )

    def run_with_proxy(self) -> None:
        osd_nodes = sorted(list(self.controller.get_nodes()["osd"].keys()))

        if self.resume_with:
            if self.resume_with not in osd_nodes:
                err_msg = f"Node {self.resume_with} not found in osd node list."
                LOGGER.error(err_msg)
                raise Exception(err_msg)

            osd_nodes = osd_nodes[osd_nodes.index(self.resume_with) :]

        self.sallogger.log(message=f"Rebooting the nodes {','.join(osd_nodes)}")
        silences = self.controller.downtime_cluster_alerts(reason="Roll rebooting OSDs")

        reboot_node_cookbook = RebootNode(spicerack=self.spicerack)
        for index, osd_node in enumerate(osd_nodes):

            if not self.force:
                self.controller.wait_for_cluster_healthy()

            LOGGER.info("Rebooting node %s, %d done, %d to go", osd_node, index, len(osd_nodes) - index)
            # we 'force' here because we want to manage the healthy state at this level,
            # not per OSD node. If the OSD manages it it will wait forever for a node to get
            # healthy while 'norebalance' is set; see T427295
            args = [
                "--fqdn-to-reboot",
                f"{osd_node}.{self.controller.get_nodes_domain()}",
                "--force",
            ] + self.common_opts.to_cli_args()

            reboot_node_cookbook.get_runner(args=reboot_node_cookbook.argument_parser().parse_args(args)).run()
            LOGGER.info(
                "Rebooted node %s, %d done, %d to go, waiting for cluster to stabilize...",
                osd_node,
                index + 1,
                len(osd_nodes) - index - 1,
            )
            self.controller.wait_for_cluster_healthy()
            LOGGER.info("Cluster stable, continuing")

        self.controller.uptime_cluster_alerts(silences=silences)
        self.sallogger.log(message=f"Finished rebooting the nodes {osd_nodes}")
