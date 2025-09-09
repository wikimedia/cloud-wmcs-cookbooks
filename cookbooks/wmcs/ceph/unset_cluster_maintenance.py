r"""WMCS Ceph - Unset cluster maintenance.

Usage example:
    cookbook wmcs.ceph.unset_cluster_maintenance \
        --cluster-name eqiad1

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.alerts import SilenceID
from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

LOGGER = logging.getLogger(__name__)


class UnSetClusterInMaintenance(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to unset the maintenance of.",
        )
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="If passed, will continue even if the cluster is not in a healthy state.",
        )
        parser.add_argument(
            "--silence-ids",
            required=False,
            default=None,
            type=lambda silences_str: [SilenceID(silence.strip()) for silence in silences_str.split(",")],
            help=(
                "Comma separated list of silences to unmute. If not passed will unmute all the silences affecting the "
                "ceph cluster alerts."
            ),
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(spicerack=self.spicerack, args=args, runner=UnSetClusterInMaintenanceRunner)(
            cluster_name=args.cluster_name,
            force=args.force,
            spicerack=self.spicerack,
            silence_ids=args.silence_ids,
        )


class UnSetClusterInMaintenanceRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        cluster_name: CephClusterName,
        force: bool,
        spicerack: Spicerack,
        common_opts: CommonOpts,
        silence_ids: list[SilenceID] | None,
    ):

        self.force = force
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.cluster_name = cluster_name
        self.silence_ids = silence_ids
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(), cluster_name=self.cluster_name, spicerack=self.spicerack
        )

    def run_with_proxy(self) -> None:

        if self.silence_ids:
            self.controller.unset_maintenance(force=self.force, silences=self.silence_ids)
        self.sallogger.log(f"Ceph cluster at {self.cluster_name} set out of maintenance")
