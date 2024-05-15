#!/usr/bin/env python3
"""Openstack Neutron specific related code."""
from __future__ import annotations

import logging
from enum import Enum

from wmcs_libs.openstack.common import NeutronAgentHAState, NeutronAgentType, OpenstackAPI, OpenstackError, wait_for_it

LOGGER = logging.getLogger(__name__)


class NeutronError(OpenstackError):
    """Neutron specific openstack error."""


class CloudnetAdminDown(NeutronError):
    """Used to say the operation failed due to the cloudnet being admin down."""


class CloudnetAdminUp(NeutronError):
    """Used to say the operation failed due to the cloudnet being admin up."""


class NetworkUnhealthy(NeutronError):
    """Happens when there's not enough agents in one of the types to serve requests."""


class NeutronAlerts(Enum):
    """list of neutron alerts and their names."""

    NEUTRON_AGENT_DOWN = "NeutronAgentDown"


# TODO: This class should eventually be folded into the OpenstackAPI
# class. It was originally separate because the Neutron CLI was separate,
# but now all the logic for talking with Neutron has been moved there already
# and only various tiny helper functions remain.
class NeutronController:
    """Neutron specific controller"""

    def __init__(self, openstack_api: OpenstackAPI):
        """Controller to handle neutron commands and operations."""
        self.openstack_api = openstack_api

    def cloudnet_set_admin_down(self, cloudnet_host: str) -> None:
        """Given a cloudnet hostname, set all it's agents down, usually for maintenance or reboot."""
        cloudnet_agents = self.openstack_api.get_neutron_agents(host=cloudnet_host)
        for agent in cloudnet_agents:
            if agent.admin_state_up:
                self.openstack_api.neutron_agent_set_admin_down(agent_id=agent.agent_id)

        self.wait_for_cloudnet_admin_down(cloudnet_host=cloudnet_host)

    def cloudnet_set_admin_up(self, cloudnet_host: str) -> None:
        """Given a cloudnet hostname, set all it's agents up, usually after maintenance or reboot."""
        cloudnet_agents = self.openstack_api.get_neutron_agents(host=cloudnet_host)
        for agent in cloudnet_agents:
            if not agent.admin_state_up:
                self.openstack_api.neutron_agent_set_admin_up(agent_id=agent.agent_id)

        self.wait_for_cloudnet_admin_up(cloudnet_host=cloudnet_host)

    def wait_for_cloudnet_admin_down(self, cloudnet_host: str) -> None:
        """Wait until the given cloudnet is set as admin down."""

        def cloudnet_admin_down():
            all_agents = self.openstack_api.get_neutron_agents()
            cloudnet_agents = [agent for agent in all_agents if agent.host == cloudnet_host]
            return all(not agent.admin_state_up for agent in cloudnet_agents)

        wait_for_it(
            condition_fn=cloudnet_admin_down,
            condition_name_msg="Cloudnet set as admin down",
            when_failed_raise_exception=CloudnetAdminUp,
            condition_failed_msg_fn=lambda: "Some cloudnet agents did not turn admin down.",
        )

    def wait_for_cloudnet_admin_up(self, cloudnet_host: str) -> None:
        """Wait until the given cloudnet is set as admin up."""

        def cloudnet_admin_up():
            all_agents = self.openstack_api.get_neutron_agents()
            cloudnet_agents = [agent for agent in all_agents if agent.host == cloudnet_host]
            return all(agent.admin_state_up for agent in cloudnet_agents)

        wait_for_it(
            condition_fn=cloudnet_admin_up,
            condition_name_msg="Cloudnet set as admin up",
            when_failed_raise_exception=CloudnetAdminDown,
            condition_failed_msg_fn=lambda: "Some cloudnet agents did not turn admin up.",
        )

    def get_cloudnets(self) -> list[str]:
        """Retrieves the known cloudnets.

        Currently does that by checking the neutron agents running on those.
        """
        return [agent.host for agent in self.openstack_api.get_neutron_agents(agent_type=NeutronAgentType.L3_AGENT)]

    def check_if_network_is_alive(self) -> None:
        """Check if the network is in a working state (all agents up and running, all routers up and running).

        Raises:
            NetworkUnhealthy if the network is not OK.

        """
        cloudnets = self.get_cloudnets()
        cloudnet_agents = [agent for agent in self.openstack_api.get_neutron_agents() if agent.host in cloudnets]
        for agent in cloudnet_agents:
            if not agent.admin_state_up or not agent.alive:
                agents_str = "\n".join(str(agent) for agent in cloudnet_agents)
                raise NetworkUnhealthy(f"Some agents are not healthy:\n{agents_str}")

        all_routers = self.openstack_api.get_routers()
        for router in all_routers:
            if not router.is_healthy():
                raise NetworkUnhealthy(f"Router {router.name} is not healthy:\n{router}")

    def wait_for_l3_handover(self):
        """Wait until there's one primary for all l3 agents.

        Used to make sure the network is working after taking one l3 agent down.
        """

        def all_routers_have_active_agent() -> bool:
            routers_down = []
            routers = self.openstack_api.get_routers()
            for router in routers:
                agents_on_router = self.openstack_api.get_neutron_agents_for_router(router_id=router.router_id)
                if not any(
                    agent.admin_state_up and agent.alive and agent.ha_state == NeutronAgentHAState.ACTIVE
                    for agent in agents_on_router
                ):
                    routers_down.append(router)
            return len(routers_down) == 0

        wait_for_it(
            condition_fn=all_routers_have_active_agent,
            condition_name_msg="all routers have a primary agent running",
            when_failed_raise_exception=NetworkUnhealthy,
            condition_failed_msg_fn=lambda: "Some routers have no primary agents",
        )

    def get_l3_primary(self) -> str:
        """Returns the cloudnet host that is primary for all l3 routers.

        NOTE: We expect all the routers to have the same primary (we only have one router for now), once we have more
        or the primaries are mixed, this should be changed.
        """
        routers = self.openstack_api.get_routers()
        for router in routers:
            agents_on_router = self.openstack_api.get_neutron_agents_for_router(router_id=router.router_id)
            for agent in agents_on_router:
                if agent.admin_state_up and agent.alive and agent.ha_state == NeutronAgentHAState.ACTIVE:
                    return agent.host

            raise NeutronError(f"Unable to find primary agent for router {router}, known agents: {agents_on_router}")

        raise NeutronError("No routers found.")

    def wait_for_network_alive(self, timeout_seconds: int = 900):
        """Wait until the network is up and running again."""

        def is_network_alive():
            try:
                self.check_if_network_is_alive()
            except NetworkUnhealthy:
                return False

            return True

        wait_for_it(
            condition_fn=is_network_alive,
            when_failed_raise_exception=NetworkUnhealthy,
            condition_name_msg="network is alive",
            condition_failed_msg_fn=lambda: "Some agents are not running",
            timeout_seconds=timeout_seconds,
        )
