r"""WMCS Openstack - Show the current cloudnets and some info.

Usage example:
    cookbook wmcs.openstack.cloudnet.show \
        --cluster_name eqiad1

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import NeutronAgentType, OpenstackAPI
from wmcs_libs.openstack.neutron import NeutronController

LOGGER = logging.getLogger(__name__)


class Show(CookbookBase):
    """WMCS Openstack cookbook to show the current status of the neutron setup."""

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
            default=OpenstackClusterName.EQIAD1,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="Site to get the info for",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        # This is a read-only cookbook, we don't want to log to SAL
        args.no_dologmsg = True
        return with_common_opts(self.spicerack, args, ShowRunner)(
            cluster_name=args.cluster_name,
            spicerack=self.spicerack,
        )


class ShowRunner(WMCSCookbookRunnerBase):
    """Runner for Show"""

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

    def run_with_proxy(self) -> None:
        """Main entry point"""
        all_agents = self.openstack_api.get_neutron_agents()
        l3_agents = [str(agent) for agent in all_agents if agent.agent_type == NeutronAgentType.L3_AGENT]
        dhcp_agents = [str(agent) for agent in all_agents if agent.agent_type == NeutronAgentType.DHCP_AGENT]
        metadata_agents = [str(agent) for agent in all_agents if agent.agent_type == NeutronAgentType.METADATA_AGENT]
        linux_bridge_agents = [
            str(agent) for agent in all_agents if agent.agent_type == NeutronAgentType.LINUX_BRIDGE_AGENT
        ]
        cloudnets = self.neutron_controller.get_cloudnets()
        routers = self.neutron_controller.router_list()
        routers_str = ""
        for router in routers:
            agents_on_router = self.neutron_controller.list_agents_hosting_router(router=router.router_id)
            routers_str += f"{router}\n        "
            routers_str += "\n        ".join(str(agent) for agent in agents_on_router)

        LOGGER.info("Got Routers:\n    %s", routers_str)
        LOGGER.info("Got L3 Agents:\n    %s", "\n    ".join(l3_agents))
        LOGGER.info("Got dhcp Agents:\n    %s", "\n    ".join(dhcp_agents))
        LOGGER.info("Got metadata Agents:\n    %s", "\n    ".join(metadata_agents))
        LOGGER.info("Got linux bridge Agents:\n    %s", "\n    ".join(linux_bridge_agents))
        LOGGER.info("Got cloudnets (should be the same as L3 agents):\n    %s", "\n    ".join(cloudnets))
