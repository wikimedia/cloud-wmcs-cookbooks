r"""WMCS OpenStack - Migrate a trove instance to OVS

Resizing should happen via the Trove API; also, we need to
kick the db guest agent after reboot to work around an
issue with recovery.

Usage example:
  cookbook wmcs.openstack.migrate_dbinstance_to_ovs \
    --cluster-name codfw1dev \
    --project project1 \
    --db-instance <id>
"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from cookbooks.wmcs.openstack.migrate_server_to_ovs import MigrateServerToOvsRunner
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName

LOGGER = logging.getLogger(__name__)


class MigrateDatabaseInstanceToOvs(CookbookBase):
    """WMCS OpenStack cookbook to migrate a project to OVS."""

    __title__ = __doc__

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
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="OpenStack cluster where the project is located.",
        )
        parser.add_argument(
            "--db-instance",
            required=True,
            type=str,
            help="ID of database instance to migrate.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, MigrateDatabaseInstanceToOvsRunner)(
            cluster_name=args.cluster_name,
            db_instance=args.db_instance,
            spicerack=self.spicerack,
        )


class MigrateDatabaseInstanceToOvsRunner(MigrateServerToOvsRunner):
    """Runner for MigrateDatabaseInstanceToOvs"""

    def __init__(
        self, common_opts: CommonOpts, cluster_name: OpenstackClusterName, db_instance: str, spicerack: Spicerack
    ):
        """Init"""
        self.db_instance: str = db_instance

        super().__init__(spicerack=spicerack, common_opts=common_opts, cluster_name=cluster_name, server="tbd")

    def run(self) -> None:
        """Main entry point"""

        # Figure out what VM corresponds to db_server
        db_instance = self.openstack_api.db_instance_show(self.db_instance)

        if db_instance["flavor"].startswith("g4"):
            LOGGER.info("Skipping %s already on g4 flavor", self.db_instance)
            return

        self.server_name = db_instance["server_id"]
        server = self.openstack_api.server_show(self.server_name)
        new_flavor = self._get_new_flavor(server)
        original_status = server["status"]

        with self._downtimed():

            if original_status != "ACTIVE":
                print("Only running Trove VMs can be resized.")

            # Make sure the guest agent is in working order
            self.openstack_api.db_instance_reboot(self.db_instance)

            self.openstack_api.db_instance_resize(self.db_instance, new_flavor_name=new_flavor)

            if original_status != "SHUTOFF":
                self.openstack_api.server_stop(self.server_name)

            self._migrate_ports()

            if original_status != "SHUTOFF":
                self.openstack_api.server_start(self.server_name)
                self.openstack_api.db_instance_reboot(self.db_instance)
