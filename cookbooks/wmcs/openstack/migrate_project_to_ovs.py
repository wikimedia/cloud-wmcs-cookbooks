r"""WMCS OpenStack - Migrate a project to OVS

Usage example:
  cookbook wmcs.openstack.migrate_project_to_ovs \
    --cluster-name codfw1dev \
    --project project1
"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.openstack.migrate_server_to_ovs import MigrateServerToOvs
from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CommonOpts,
    WMCSCookbookRunnerBase,
    add_common_opts,
    with_common_opts,
)
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class MigrateProjectToOvs(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="OpenStack cluster where the project is located.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(self.spicerack, args, MigrateProjectToOvsRunner)(
            cluster_name=args.cluster_name,
            spicerack=self.spicerack,
        )


class MigrateProjectToOvsRunner(WMCSCookbookRunnerBase):

    def __init__(self, common_opts: CommonOpts, cluster_name: OpenstackClusterName, spicerack: Spicerack):

        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=cluster_name,
            project=common_opts.project,
        )

        self.cluster_name: OpenstackClusterName = cluster_name
        self.common_opts: CommonOpts = common_opts
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run(self) -> int:

        migrate_server_cookbook = MigrateServerToOvs(spicerack=self.spicerack)
        fail = False

        for server in self.openstack_api.server_list(cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT):
            if "g4" in server["Flavor"]:
                LOGGER.info("Skipping %s already on g4 flavor", server["Name"])
                continue
            LOGGER.info("Migrating %s", server["Name"])
            runner = migrate_server_cookbook.get_runner(
                args=migrate_server_cookbook.argument_parser().parse_args(
                    [
                        "--cluster-name",
                        self.cluster_name.value,
                        "--server",
                        server["Name"],
                    ]
                    + self.common_opts.to_cli_args(),
                )
            )

            try:
                runner.run()
            except Exception:  # pylint: disable=broad-except
                LOGGER.warning("Failed to migrate %s", server["Name"], exc_info=True)
                fail = True

        return 1 if fail else 0
