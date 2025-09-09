r"""WMCS Ceph - Reboot a single ceph node.

Usage example:
    cookbook wmcs.ceph.wait_for_rebalance \
        --cluster eqiad1

"""

from __future__ import annotations

import argparse
import datetime
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

LOGGER = logging.getLogger(__name__)


class WaitForRebalance(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to roll restart.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(
            self.spicerack,
            args,
            WaitForRebalanceRunner,
        )(
            cluster_name=args.cluster_name,
            spicerack=self.spicerack,
        )


class WaitForRebalanceRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: CephClusterName,
        spicerack: Spicerack,
    ):

        self.common_opts = common_opts
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(),
            cluster_name=cluster_name,
            spicerack=self.spicerack,
        )

    def run_with_proxy(self) -> None:

        self.sallogger.log(message="Waiting for cluster to finish rebalancing...")

        self.controller.wait_for_rebalance(timeout=datetime.timedelta(hours=10))

        self.sallogger.log(message="Rebalance finished \\o/")
