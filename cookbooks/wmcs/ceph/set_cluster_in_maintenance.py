r"""WMCS Ceph - Set cluster in maintenance.

Usage example:
    cookbook wmcs.ceph.set_cluster_in_maintenance \
        --cluster-name eqiad1 \
        --reason "Doing some tests or similar"

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

LOGGER = logging.getLogger(__name__)


class SetClusterInMaintenance(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to set in maintenance.",
        )
        parser.add_argument(
            "--reason",
            required=True,
            help="Reason for the maintenance.",
        )
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="If passed, will continue even if the cluster is not in a healthy state.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(spicerack=self.spicerack, args=args, runner=SetClusterInMaintenanceRunner)(
            cluster_name=args.cluster_name,
            force=args.force,
            spicerack=self.spicerack,
            reason=args.reason,
        )


class SetClusterInMaintenanceRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        cluster_name: CephClusterName,
        force: bool,
        spicerack: Spicerack,
        common_opts: CommonOpts,
        reason: str,
    ):

        self.cluster_name = cluster_name
        self.force = force
        self.reason = reason
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(), cluster_name=cluster_name, spicerack=self.spicerack
        )

    def run_with_proxy(self) -> None:

        silences = self.controller.set_maintenance(force=self.force, reason=self.reason)
        self.sallogger.log(
            f"Set the ceph cluster for {self.cluster_name} in maintenance, alert silence ids: {','.join(silences)}"
        )
