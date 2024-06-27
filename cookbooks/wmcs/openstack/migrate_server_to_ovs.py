r"""WMCS OpenStack - Migrate a server to OVS

Usage example:
  cookbook wmcs.openstack.migrate_server_to_ovs \
    --cluster-name codfw1dev \
    --project project1 \
    --instance instance1
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from spicerack import Spicerack, SpicerackError
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class MigrateServerToOvs(CookbookBase):
    """WMCS OpenStack cookbook to migrate a server to OVS."""

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
            help="Openstack cluster_name where the instance is hosted.",
        )
        parser.add_argument(
            "--server",
            required=True,
            help="Name of the server to migrate",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, MigrateServerToOvsRunner)(
            cluster_name=args.cluster_name,
            server=args.server,
            spicerack=self.spicerack,
        )


class MigrateServerToOvsRunner(WMCSCookbookRunnerBase):
    """Runner for MigrateServerToOvs"""

    def __init__(self, common_opts: CommonOpts, cluster_name: OpenstackClusterName, server: str, spicerack: Spicerack):
        """Init"""
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=cluster_name,
            project=common_opts.project,
        )

        self.cluster_name: OpenstackClusterName = cluster_name
        self.project_id: str = common_opts.project
        self.server_name: str = server
        super().__init__(spicerack=spicerack, common_opts=common_opts)

        self.admin_reason = self.spicerack.admin_reason(
            "Moving server to a hypervisor using the OVS network agent", common_opts.task_id
        )

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for server {self.server_name}"

    def _get_new_flavor(self, server_data: dict[str, Any]) -> str:
        """Get the flavor this server is being migrated to."""
        # the 'flavor' property is something like "g3.cores2.ram4.disk20 (g3.cores2.ram4.disk20)"
        old_flavor: str = server_data["flavor"].split(" ")[0]

        flavor_map = {
            "g3.cores16.ram34.disk20": "g4.cores16.ram32.disk20",
            "g3.cores16.ram36.disk20": "g4.cores16.ram32.disk20",
            "g3.cores8.ram36.disk20": "g4.cores8.ram32.disk20",
            "g2.cores1.ram2.disk20": "g4.cores1.ram2.disk20",
        }

        if old_flavor in (
            "g3.cores1.ram1.disk20",
            "g3.cores1.ram2.disk20",
            "g3.cores2.ram4.disk20",
            "g3.cores4.ram8.disk20",
            "g3.cores8.ram16.disk20",
            "g3.cores8.ram32.disk20",
            "g3.cores4.ram8.disk20.ephem40",
            "g3.cores8.ram16.disk20.ephem140",
            "g3.cores8.ram24.disk20.ephemeral40.4xiops",
            "g3.cores8.ram24.disk20.ephemeral90.4xiops",
            "g3.cores16.ram16.disk20",
            "g3.cores16.ram32.disk20",
            "g3.cores16.ram64.disk20.10xiops",
            "g3.cores8.ram36.disk20.4xiops",
            "g3.cores8.ram24.disk20.ephemeral60.4xiops",
            "g3.cores1.ram2.disk20.localdisk",
            "g3.cores2.ram4.disk20.localdisk",
        ):
            return old_flavor.replace("g3.", "g4.")
        if old_flavor in flavor_map:
            return flavor_map[old_flavor]

        raise RuntimeError(f"Unable to determine new flavor to replace '{old_flavor}'")

    @contextmanager
    def _downtimed(self) -> Iterator[None]:
        """Set a short downtime to prevent InstanceDown alerts before Prometheus notices the server was removed."""
        # This is copy-pasted from wmcs.vps.remove_instance until we have a better
        # abstraction in Spicerack or elsewhere. (T364733)

        try:
            alertmanager = self.spicerack.alertmanager(instance_name=f"metricsinfra-{self.cluster_name.value}")
        except SpicerackError as e:
            # Most likely this means we're running in codfw1dev, or on a local setup with no metricsinfra configuration.
            LOGGER.info("Not downtiming alerts because Alertmanager is not available: %s", str(e))
            yield
            return

        with alertmanager.downtimed(
            reason=self.admin_reason,
            matchers=[
                {"name": "project", "value": self.project_id, "isRegex": False},
                {"name": "instance", "value": self.server_name, "isRegex": False},
            ],
            duration=timedelta(minutes=15),
        ):
            yield

    def _migrate_ports(self) -> None:
        """Migrate the Neutron port to the OVS driver."""
        remote_cloudcontrol = self.openstack_api.control_node
        server_id = self.openstack_api.server_show(self.server_name)["id"]
        ports = self.openstack_api.port_get_for_server(server_id)

        for port in ports:
            LOGGER.info("Updating port %s", port.port_id)
            remote_cloudcontrol.run_sync(
                'mariadb neutron -u root -e "UPDATE ml2_port_bindings '  # nosec - hardcoded_sql_expressions
                f"SET vif_type = 'ovs' WHERE port_id = '{port.port_id}';\"",
                print_progress_bars=False,
            )

    def run(self) -> None:
        """Main entry point"""
        server = self.openstack_api.server_show(self.server_name)
        new_flavor = self._get_new_flavor(server)
        original_status = server["status"]

        with self._downtimed():
            if original_status != "SHUTOFF":
                self.openstack_api.server_stop(self.server_name)

            self.openstack_api.server_resize(self.server_name, new_flavor_name=new_flavor)
            self._migrate_ports()

            if original_status != "SHUTOFF":
                self.openstack_api.server_start(self.server_name)
