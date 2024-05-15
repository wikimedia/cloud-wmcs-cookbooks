from __future__ import annotations

from unittest.mock import MagicMock, patch

import cumin
import pytest

from wmcs_libs.common import UtilsForTesting
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import (
    NeutronAgentType,
    NeutronPartialAgent,
    NeutronPartialRouter,
    NeutronRouterStatus,
    OpenstackAPI,
)
from wmcs_libs.openstack.neutron import NetworkUnhealthy, NeutronController


def get_stub_agent(
    agent_id: str = "dummyagentid",
    agent_type: NeutronAgentType = NeutronAgentType.L3_AGENT,
    host: str = "dummyhost",
    availability_zone: str | None = "dummyavailabilityzone",
    binary: str | None = "dummybinary",
    admin_state_up: bool = True,
    alive: bool = True,
) -> NeutronPartialAgent:
    return NeutronPartialAgent(
        agent_id=agent_id,
        agent_type=agent_type,
        host=host,
        availability_zone=availability_zone,
        binary=binary,
        admin_state_up=admin_state_up,
        alive=alive,
    )


def get_stub_router(
    router_id: str = "dummyrouterid",
    status: NeutronRouterStatus = NeutronRouterStatus.ACTIVE,
    has_ha: bool = True,
    admin_state_up: bool = True,
) -> NeutronPartialRouter:
    return NeutronPartialRouter(
        admin_state_up=admin_state_up,
        has_ha=has_ha,
        router_id=router_id,
        name="cloudinstances2b-gw",
        status=status,
        tenant_id="admin",
    )


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "No cloudnets": {
                # neutron expects a first spurious line
                "neutron_output": "\n[]",
                "expected_cloudnets": [],
            },
            "L3 agent": {
                "neutron_output": """
                    [
                        {
                            "ID": "3f54b3c2-503f-4667-8263-859a259b3b21",
                            "Agent Type": "L3 agent",
                            "Host": "cloudnet1006",
                            "Availability Zone": "nova",
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-l3-agent"
                        }
                    ]
                """,
                "expected_cloudnets": ["cloudnet1006"],
            },
            "More than one agent": {
                "neutron_output": """
                    [
                        {
                            "ID": "6a88c860-29fb-4a85-8aea-6a8877c2e035",
                            "Agent Type": "L3 agent",
                            "Host": "cloudnet1005",
                            "Availability Zone": "nova",
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-l3-agent"
                        },
                        {
                            "ID": "3f54b3c2-503f-4667-8263-859a259b3b21",
                            "Agent Type": "L3 agent",
                            "Host": "cloudnet1006",
                            "Availability Zone": "nova",
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-l3-agent"
                        }
                    ]
                """,
                "expected_cloudnets": ["cloudnet1005", "cloudnet1006"],
            },
        }
    )
)
def test_NeutronController_get_cloudnets_works(neutron_output: str, expected_cloudnets: list[str]):
    fake_remote = UtilsForTesting.get_fake_remote(responses=[neutron_output])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    my_controller = NeutronController(openstack_api=my_api)
    fake_run_sync = fake_remote.query.return_value.run_sync

    gotten_agents = my_controller.get_cloudnets()

    assert sorted(gotten_agents) == sorted(expected_cloudnets)
    fake_run_sync.assert_called_with(
        cumin.transports.Command(
            "env OS_PROJECT_ID=admin-monitoring wmcs-openstack network agent list --agent-type=l3 -f json --os-cloud novaadmin",  # noqa: E501
            ok_codes=[0],
        ),
        is_safe=True,
        print_output=False,
        print_progress_bars=False,
        success_threshold=1,
        batch_size=None,
        batch_sleep=None,
    )


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "No agents and no routers": {
                "agents": [],
                "routers": [],
            },
            "All agent and routers ok": {
                "agents": [
                    get_stub_agent(agent_id="agent1", admin_state_up=True, alive=True),
                    get_stub_agent(agent_id="agent2", admin_state_up=True, alive=True),
                ],
                "routers": [
                    get_stub_router(router_id="router1", admin_state_up=True, has_ha=True),
                    get_stub_router(router_id="router2", admin_state_up=True, has_ha=True),
                ],
            },
        }
    )
)
def test_NeutronController_check_if_network_is_alive_does_not_raise(
    agents: list[NeutronPartialAgent], routers: list[NeutronPartialRouter]
):
    # just in case a call gets through
    fake_remote = UtilsForTesting.get_fake_remote(responses=[])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    my_controller = NeutronController(openstack_api=my_api)

    with patch.object(my_api, "get_neutron_agents", MagicMock(return_value=agents)), patch.object(
        my_api, "get_routers", MagicMock(return_value=routers)
    ):
        # assert it does not raise
        my_controller.check_if_network_is_alive()


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "One agent dead, routers ok": {
                "agents": [
                    get_stub_agent(agent_id="agent1", admin_state_up=True, alive=True),
                    get_stub_agent(agent_id="agent2", admin_state_up=True, alive=False),
                ],
                "routers": [
                    get_stub_router(router_id="router1", admin_state_up=True, has_ha=True),
                    get_stub_router(router_id="router2", admin_state_up=True, has_ha=True),
                ],
            },
            "One agent admin down, routers ok": {
                "agents": [
                    get_stub_agent(agent_id="agent1", admin_state_up=True, alive=True),
                    get_stub_agent(agent_id="agent2", admin_state_up=False, alive=True),
                ],
                "routers": [
                    get_stub_router(router_id="router1", admin_state_up=True, has_ha=True),
                    get_stub_router(router_id="router2", admin_state_up=True, has_ha=True),
                ],
            },
            "Agents ok, one router not ha": {
                "agents": [
                    get_stub_agent(agent_id="agent1", admin_state_up=True, alive=True),
                    get_stub_agent(agent_id="agent2", admin_state_up=True, alive=True),
                ],
                "routers": [
                    get_stub_router(router_id="router1", admin_state_up=True, has_ha=True),
                    get_stub_router(router_id="router2", admin_state_up=True, has_ha=False),
                ],
            },
            "Agents ok, one router admin down": {
                "agents": [
                    get_stub_agent(agent_id="agent1", admin_state_up=True, alive=True),
                    get_stub_agent(agent_id="agent2", admin_state_up=True, alive=True),
                ],
                "routers": [
                    get_stub_router(router_id="router1", admin_state_up=True, has_ha=True),
                    get_stub_router(router_id="router2", admin_state_up=False, has_ha=True),
                ],
            },
        }
    )
)
def test_NeutronController_check_if_network_is_alive_raises(
    agents: list[NeutronPartialAgent], routers: list[NeutronPartialRouter]
):
    # just in case a call gets through
    fake_remote = UtilsForTesting.get_fake_remote(responses=[])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    my_controller = NeutronController(openstack_api=my_api)

    with patch.object(my_api, "get_neutron_agents", MagicMock(return_value=agents)), patch.object(
        my_api, "get_routers", MagicMock(return_value=routers)
    ):
        with pytest.raises(NetworkUnhealthy):
            my_controller.check_if_network_is_alive()
