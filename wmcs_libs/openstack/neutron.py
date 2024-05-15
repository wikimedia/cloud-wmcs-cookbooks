#!/usr/bin/env python3
"""Openstack Neutron specific related code."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from wmcs_libs.common import (
    CUMIN_SAFE_WITH_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    CommandRunnerMixin,
    CuminParams,
    OutputFormat,
)
from wmcs_libs.openstack.common import (
    NeutronAgentType,
    NeutronPartialAgent,
    OpenstackAPI,
    OpenstackError,
    OpenstackID,
    OpenstackIdentifier,
    wait_for_it,
)

LOGGER = logging.getLogger(__name__)


class NeutronError(OpenstackError):
    """Neutron specific openstack error."""


class IncompleteData(NeutronError):
    """Thrown when trying to act on an object without having loaded all it's data."""


class CloudnetsUnhealthy(NeutronError):
    """Happens when some of the cloudnets are not in a healthy state."""


class CloudnetAdminDown(NeutronError):
    """Used to say the operation failed due to the cloudnet being admin down."""


class CloudnetAdminUp(NeutronError):
    """Used to say the operation failed due to the cloudnet being admin up."""


class NetworkUnhealthy(NeutronError):
    """Happens when there's not enough agents in one of the types to serve requests."""


class NeutronAlerts(Enum):
    """list of neutron alerts and their names."""

    NEUTRON_AGENT_DOWN = "NeutronAgentDown"


class NeutronAgentHAState(Enum):
    """HA state for a neutron agent."""

    ACTIVE = "active"
    STANDBY = "standby"


@dataclass(frozen=True)
class NeutronAgent(NeutronPartialAgent):
    """Full Neutron agent info."""

    ha_state: NeutronAgentHAState | None = None

    @classmethod
    def from_agent_data(cls, agent_data: dict[str, Any]) -> "NeutronAgent":
        """Get a NetworkAgent passing the agent_data as returned by the neutron cli."""
        return cls(
            host=agent_data["host"],
            agent_type=NeutronAgentType(agent_data["agent_type"]),
            admin_state_up=agent_data["admin_state_up"],
            alive=agent_data["alive"] == ":-)",
            agent_id=agent_data["id"],
            # This will only be null on (broken) things still using the Neutron CLI.
            binary=agent_data.get("binary", ""),
            availability_zone=agent_data.get("availability_zone", None),
            ha_state=NeutronAgentHAState(agent_data["ha_state"]) if "ha_state" in agent_data else None,
        )

    def __str__(self) -> str:
        """Return the string representation of this class."""
        return (
            f"{self.host} ({self.agent_type}): "
            f"{'ADMIN_UP' if self.admin_state_up else 'ADMIN_DOWN'} "
            f"{'ALIVE' if self.alive else 'DEAD'} "
            f"ha_state:{self.ha_state if self.ha_state is not None else 'NotFetched'} "
            f"id:{self.agent_id} "
            f"binary:{self.binary if self.binary is not None else 'NotFetched'}"
        )


class NeutronController(CommandRunnerMixin):
    """Neutron specific controller"""

    def __init__(self, openstack_api: OpenstackAPI):
        """Controller to handle neutron commands and operations."""
        self.openstack_api = openstack_api
        self.control_node = openstack_api.control_node
        super().__init__(command_runner_node=self.control_node)

    def _get_full_command(self, *command: str, json_output: bool = True, project_as_arg: bool = False):
        cmd = ["source", "/root/novaenv.sh", "&&", "neutron", *command]
        if json_output:
            cmd.extend(["--format", "json"])

        script = " ".join(cmd)
        # we need sudo, and the sourced credentials, so we have to wrap it in a bash command
        return ["bash", "-c", f"'{script}'"]

    def run_formatted_as_list(
        self,
        *command,
        capture_errors: bool = False,
        project_as_arg: bool = False,
        skip_first_line: bool = True,
        cumin_params: CuminParams | None = None,
    ) -> list[Any]:
        """Run a neutron command on a control node forcing json output."""
        # neutron command return a first line in the output that is a warning, not part of the json
        return super().run_formatted_as_list(
            *command,
            skip_first_line=skip_first_line,
            capture_errors=capture_errors,
            project_as_arg=project_as_arg,
            cumin_params=CuminParams.replace(cumin_params, print_output=False, print_progress_bars=False),
        )

    def run_formatted_as_dict(
        self,
        *command: str,
        capture_errors: bool = False,
        skip_first_line: bool = True,
        project_as_arg: bool = False,
        cumin_params: CuminParams | None = None,
        try_format: OutputFormat = OutputFormat.JSON,
        last_line_only: bool = False,
    ) -> dict[str, Any]:
        """Run a neutron command on a control node forcing json output."""
        return super().run_formatted_as_dict(
            *command,
            capture_errors=capture_errors,
            cumin_params=CuminParams.replace(cumin_params, print_output=False, print_progress_bars=False),
            skip_first_line=skip_first_line,
            last_line_only=last_line_only,
            try_format=try_format,
            project_as_arg=project_as_arg,
        )

    def _run_one_raw(self, *command: str, json_output: bool = False) -> str:
        """Run a neutron command on a control node returning the raw string."""
        return super().run_raw(*command, json_output=json_output, cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

    def agent_set_admin_up(self, agent_id: OpenstackID) -> None:
        """Set the given agent as admin-state-up (online)."""
        self._run_one_raw("agent-update", "--admin-state-up", agent_id, json_output=False)

    def agent_set_admin_down(self, agent_id: OpenstackID) -> None:
        """Set the given agent as admin-state-down (offline)."""
        self._run_one_raw("agent-update", "--admin-state-down", agent_id, json_output=False)

    def cloudnet_set_admin_down(self, cloudnet_host: str) -> None:
        """Given a cloudnet hostname, set all it's agents down, usually for maintenance or reboot."""
        cloudnet_agents = self.openstack_api.get_neutron_agents(host=cloudnet_host)
        for agent in cloudnet_agents:
            if agent.admin_state_up:
                self.agent_set_admin_down(agent_id=agent.agent_id)

        self.wait_for_cloudnet_admin_down(cloudnet_host=cloudnet_host)

    def cloudnet_set_admin_up(self, cloudnet_host: str) -> None:
        """Given a cloudnet hostname, set all it's agents up, usually after maintenance or reboot."""
        cloudnet_agents = self.openstack_api.get_neutron_agents(host=cloudnet_host)
        for agent in cloudnet_agents:
            if not agent.admin_state_up:
                self.agent_set_admin_up(agent_id=agent.agent_id)

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

    def list_agents_hosting_router(self, router: OpenstackIdentifier) -> list[NeutronAgent]:
        """Get the list of nodes hosting a given router routers."""
        return [
            NeutronAgent.from_agent_data(agent_data={**agent_data, "agent_type": NeutronAgentType.L3_AGENT.value})
            for agent_data in self.run_formatted_as_list(
                "l3-agent-list-hosting-router", router, cumin_params=CUMIN_SAFE_WITH_OUTPUT
            )
        ]

    def get_cloudnets(self) -> list[str]:
        """Retrieves the known cloudnets.

        Currently does that by checking the neutron agents running on those.
        """
        return [
            agent.host
            for agent in self.openstack_api.get_neutron_agents()
            if agent.agent_type == NeutronAgentType.L3_AGENT
        ]

    def list_routers_on_agent(self, agent_id: OpenstackID) -> list[dict[str, Any]]:
        """Get the list of routers hosted a given agent."""
        return self.run_formatted_as_list("router-list-on-l3-agent", agent_id, cumin_params=CUMIN_SAFE_WITH_OUTPUT)

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
                agents_on_router = self.list_agents_hosting_router(router=router.router_id)
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
            agents_on_router = self.list_agents_hosting_router(router=router.router_id)
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
