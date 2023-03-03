r"""WMCS Ceph - Unset cluster maintenance.

Usage example:
    cookbook wmcs.ceph.unset_cluster_maintenance \
        --cluster-name eqiad1

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.alerts import SilenceID
from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory import CephClusterName

LOGGER = logging.getLogger(__name__)


class UnSetClusterInMaintenance(CookbookBase):
    """WMCS Ceph cookbook to unset a cluster maintenance."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
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
            type=lambda silences_str: [silence.strip() for silence in silences_str.split(",")],
            help=(
                "Comma separated list of silences to unmute. If not passed will unmute all the silences affecting the "
                "ceph cluster alerts."
            ),
        )
        add_common_opts(parser)

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(spicerack=self.spicerack, args=args, runner=UnSetClusterInMaintenanceRunner)(
            cluster_name=args.cluster_name,
            force=args.force,
            spicerack=self.spicerack,
            silence_ids=args.silence_ids,
        )


class UnSetClusterInMaintenanceRunner(WMCSCookbookRunnerBase):
    """Runner for UnSetClusterInMaintenance"""

    def __init__(
        self,
        cluster_name: CephClusterName,
        force: bool,
        spicerack: Spicerack,
        common_opts: CommonOpts,
        silence_ids: list[SilenceID] | None,
    ):
        """Init"""
        self.force = force
        super().__init__(spicerack=spicerack)
        self.cluster_name = cluster_name
        self.silence_ids = silence_ids
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )
        self.controller = CephClusterController(
            remote=self.spicerack.remote(), cluster_name=self.cluster_name, spicerack=self.spicerack
        )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        self.controller.unset_maintenance(force=self.force, silences=self.silence_ids)
        self.sallogger.log(f"Ceph cluster at {self.cluster_name} set out of maintenance")
