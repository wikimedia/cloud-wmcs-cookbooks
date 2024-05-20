r"""WMCS Openstack - Migrate active cloudnet node to Open vSwitch agent

Usage example:
    cookbook wmcs.openstack.cloudnet.migrate_to_ovs \
        --cluster-name eqiad1

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import NeutronAgentType, OpenstackAPI
from wmcs_libs.openstack.neutron import NeutronController

LOGGER = logging.getLogger(__name__)


class MigrateToOvs(CookbookBase):
    """One-off cookbook to migrate the active OVS node to the one running OVS."""

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
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="Site to run the migration on",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, MigrateToOvsRunner)(
            cluster_name=args.cluster_name,
            spicerack=self.spicerack,
        )


class MigrateToOvsRunner(WMCSCookbookRunnerBase):
    """Runner for MigrateToOvs"""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: OpenstackClusterName,
        spicerack: Spicerack,
    ):
        """Init"""
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.openstack_api = OpenstackAPI(
            remote=self.spicerack.remote(),
            cluster_name=cluster_name,
            project="admin",
        )
        self.neutron_controller = NeutronController(openstack_api=self.openstack_api)

        self.node_from, self.node_to, self.is_rollback = self._find_nodes()

        direction = "roll back from OVS to linuxbridge" if self.is_rollback else "migrate from linuxbridge to OVS"
        ask_confirmation(f"Are you ready to {direction} from {self.node_from} to {self.node_to}?")

    def _find_nodes(self) -> tuple[str, str, bool]:
        """Find the host names and the direction to run the migration on."""

        cloudnets = self.neutron_controller.get_cloudnets()
        cloudnet_active = self.neutron_controller.get_l3_primary()
        cloudnets_with_ovs = [
            agent.host
            for agent in self.openstack_api.get_neutron_agents(agent_type=NeutronAgentType.OVS_AGENT)
            if agent.host in cloudnets
        ]
        cloudnets_with_linuxbridge = [
            agent.host
            for agent in self.openstack_api.get_neutron_agents(agent_type=NeutronAgentType.LINUX_BRIDGE_AGENT)
            if agent.host in cloudnets
        ]
        if len(cloudnets_with_ovs) != 1 or len(cloudnets_with_linuxbridge) != 1:
            raise Exception(f"Found too many nodes! {cloudnets_with_ovs} {cloudnets_with_linuxbridge}")

        node_from = cloudnets_with_linuxbridge[0]
        node_to = cloudnets_with_ovs[0]
        is_rollback = False
        if cloudnet_active == node_to:
            node_from, node_to = node_to, node_from
            is_rollback = True
        elif cloudnet_active != node_from:
            raise Exception(
                f"Got invalid active cloudnet! {cloudnets_with_ovs} {cloudnets_with_linuxbridge} {cloudnet_active}"
            )
        return node_from, node_to, is_rollback

    def run(self) -> None:
        """Main entry point"""
        domain = self.openstack_api.get_nodes_domain()
        remote_from = self.spicerack.remote().query(f"D{{{self.node_from}.{domain}}}", use_sudo=True)
        remote_to = self.spicerack.remote().query(f"D{{{self.node_to}.{domain}}}", use_sudo=True)
        remote_cloudcontrol = self.openstack_api.control_node

        LOGGER.info("Stopping services on former host %s", self.node_from)
        remote_from.run_sync(
            "systemctl stop neutron-metadata-agent.service neutron-dhcp-agent.service "
            f"neutron-{'openvswitch' if self.is_rollback else 'linuxbridge'}-agent.service neutron-l3-agent.service"
        )

        LOGGER.info("Fixing ports in the database on cloudcontrol %s", self.openstack_api.control_node_fqdn)
        remote_cloudcontrol.run_sync(
            'mariadb neutron -u root -e "UPDATE ml2_port_bindings '  # nosec - hardcoded_sql_expressions
            f'SET vif_type = \'{"linuxbridge" if self.is_rollback else "ovs"}\' '
            'WHERE port_id IN (SELECT port_id FROM routerports);"'
        )

        LOGGER.info("Starting services on new host %s", self.node_to)
        remote_to.run_sync(
            "systemctl start neutron-metadata-agent.service neutron-dhcp-agent.service "
            f"neutron-{'linuxbridge' if self.is_rollback else 'openvswitch'}-agent.service neutron-l3-agent.service"
        )
