r"""WMCS OpenStack - Rebuild a trove instance with a new guest image

This is slightly more complicated than 'openstack server instance rebuild'
because we need to clear snapshots beforehand; otherwise the snapshots created
by the backup system prevent the rebuild.

Usage example:
  cookbook wmcs.openstack.rebuild_dbinstance \
    --cluster-name codfw1dev \
    --project project1 \
    --db-instance <id>
    --guest-image <id>
"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI
from wmcs_libs.openstack.rbd import RBDRunner

LOGGER = logging.getLogger(__name__)


class RebuildDatabaseInstance(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="OpenStack cluster where the db instance is located.",
        )
        parser.add_argument(
            "--db-instance",
            required=True,
            type=str,
            help="ID of database instance to rebuild.",
        )
        parser.add_argument(
            "--guest-image",
            required=True,
            type=str,
            help="ID of new Trove guest image to rebuild with.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(self.spicerack, args, RebuildDatabaseInstanceRunner)(
            cluster_name=args.cluster_name,
            db_instance=args.db_instance,
            guest_image=args.guest_image,
            spicerack=self.spicerack,
        )


class RebuildDatabaseInstanceRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: OpenstackClusterName,
        db_instance: str,
        guest_image: str,
        spicerack: Spicerack,
    ):

        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=cluster_name,
            project=common_opts.project,
        )

        pool_name = f"{cluster_name}-compute"

        self.rbd_runner = RBDRunner(
            remote=spicerack.remote(),
            pool_name=pool_name,
            cluster_name=cluster_name,
        )

        self.cluster_name: OpenstackClusterName = cluster_name
        self.db_instance: str = db_instance
        self.guest_image: str = guest_image

        super().__init__(spicerack=spicerack, common_opts=common_opts)

        self.admin_reason = self.spicerack.admin_reason(
            "Rebuilding database instance with new guest image", common_opts.task_id
        )

    def run(self) -> None:

        # Figure out what VM corresponds to db_server
        db_instance = self.openstack_api.db_instance_show(self.db_instance)

        server_id = db_instance["server_id"]

        self.rbd_runner.purge_server_snapshots(server_id)
        self.openstack_api.db_instance_rebuild(self.db_instance, self.guest_image)
