from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import cumin
import pytest

from wmcs_libs.common import UtilsForTesting
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import NeutronAgentType, OpenstackAPI
from wmcs_libs.openstack.neutron import (
    NetworkUnhealthy,
    NeutronAgent,
    NeutronAgentHAState,
    NeutronController,
    NeutronPartialRouter,
    NeutronRouter,
    NeutronRouterStatus,
)


def get_stub_agent(
    agent_id: str = "dummyagentid",
    agent_type: NeutronAgentType = NeutronAgentType.L3_AGENT,
    ha_state: NeutronAgentHAState | None = None,
    host: str = "dummyhost",
    availability_zone: str | None = "dummyavailabilityzone",
    binary: str | None = "dummybinary",
    admin_state_up: bool = True,
    alive: bool = True,
) -> NeutronAgent:
    return NeutronAgent(
        agent_id=agent_id,
        agent_type=agent_type,
        ha_state=ha_state,
        host=host,
        availability_zone=availability_zone,
        binary=binary,
        admin_state_up=admin_state_up,
        alive=alive,
    )


def partial_router_from_full_router(router: NeutronRouter) -> NeutronPartialRouter:
    return NeutronPartialRouter(
        has_ha=router.has_ha,
        router_id=router.router_id,
        name=router.name,
        tenant_id=router.tenant_id,
    )


def get_stub_router(
    router_id: str = "dummyrouterid",
    status: NeutronRouterStatus = NeutronRouterStatus.ACTIVE,
    has_ha: bool = True,
    admin_state_up: bool = True,
) -> NeutronRouter:
    return NeutronRouter(
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
            "No routers": {
                # neutron expects a first line that will be discarded
                "neutron_output": "\n[]",
                "expected_routers": [],
            },
            "One router": {
                "neutron_output": """
                    [
                        {
                            "id": "d93771ba-2711-4f88-804a-8df6fd03978a",
                            "name": "cloudinstances2b-gw",
                            "tenant_id": "admin",
                            "external_gateway_info": {
                                "network_id": "5c9ee953-3a19-4e84-be0f-069b5da75123",
                                "external_fixed_ips": [
                                    {
                                    "subnet_id": "77dba34f-c8f2-4706-a0b6-2a8ed4d91f51",
                                    "ip_address": "185.15.56.238"
                                    }
                                ],
                                "enable_snat": false
                            },
                            "distributed": false,
                            "ha": true
                        }
                    ]
                """,
                "expected_routers": [
                    NeutronPartialRouter(
                        router_id="d93771ba-2711-4f88-804a-8df6fd03978a",
                        name="cloudinstances2b-gw",
                        tenant_id="admin",
                        has_ha=True,
                    )
                ],
            },
            "Many routers": {
                "neutron_output": """
                    [
                        {
                            "id": "d93771ba-2711-4f88-804a-8df6fd03978a",
                            "name": "cloudinstances2b-gw",
                            "tenant_id": "admin",
                            "external_gateway_info": {
                                "network_id": "5c9ee953-3a19-4e84-be0f-069b5da75123",
                                "external_fixed_ips": [
                                    {
                                    "subnet_id": "77dba34f-c8f2-4706-a0b6-2a8ed4d91f51",
                                    "ip_address": "185.15.56.238"
                                    }
                                ],
                                "enable_snat": false
                            },
                            "distributed": false,
                            "ha": true
                        },
                        {
                            "id": "d93771ba-2711-4f88-804a-8df6fd03978b",
                            "name": "cloudinstances2c-gw",
                            "tenant_id": "admin",
                            "external_gateway_info": {
                                "network_id": "5c9ee953-3a19-4e84-be0f-069b5da75124",
                                "external_fixed_ips": [
                                    {
                                    "subnet_id": "77dba34f-c8f2-4706-a0b6-2a8ed4d91f52",
                                    "ip_address": "185.15.56.239"
                                    }
                                ],
                                "enable_snat": false
                            },
                            "distributed": false,
                            "ha": true
                        }
                    ]
                """,
                "expected_routers": [
                    NeutronPartialRouter(
                        router_id="d93771ba-2711-4f88-804a-8df6fd03978a",
                        name="cloudinstances2b-gw",
                        tenant_id="admin",
                        has_ha=True,
                    ),
                    NeutronPartialRouter(
                        router_id="d93771ba-2711-4f88-804a-8df6fd03978b",
                        name="cloudinstances2c-gw",
                        tenant_id="admin",
                        has_ha=True,
                    ),
                ],
            },
        }
    )
)
def test_NeutronController_router_list_works(neutron_output: str, expected_routers: list[NeutronAgent]):
    fake_remote = UtilsForTesting.get_fake_remote(responses=[neutron_output])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    my_controller = NeutronController(openstack_api=my_api)
    fake_run_sync = fake_remote.query.return_value.run_sync

    gotten_routers = my_controller.router_list()

    assert gotten_routers == expected_routers
    fake_run_sync.assert_called_with(
        cumin.transports.Command(
            "bash -c 'source /root/novaenv.sh && neutron router-list --format json'",
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
            "No nodes": {
                # neutron expects a first spurious line
                "neutron_output": "\n[]",
                "expected_agents": [],
            },
            "One node": {
                "neutron_output": """
                    [
                        {
                            "id": "4be214c8-76ef-40f8-9d5d-4c344d213311",
                            "host": "cloudnet1003",
                            "admin_state_up": true,
                            "alive": ":-)",
                            "ha_state": "standby"
                        }
                    ]
                """,
                "expected_agents": [
                    NeutronAgent(
                        agent_type=NeutronAgentType.L3_AGENT,
                        agent_id="4be214c8-76ef-40f8-9d5d-4c344d213311",
                        host="cloudnet1003",
                        admin_state_up=True,
                        alive=True,
                        ha_state=NeutronAgentHAState.STANDBY,
                        availability_zone=None,
                        binary="",
                    ),
                ],
            },
            "Many nodes": {
                "neutron_output": """
                    [
                        {
                            "id": "4be214c8-76ef-40f8-9d5d-4c344d213311",
                            "host": "cloudnet1003",
                            "admin_state_up": true,
                            "alive": ":-)",
                            "ha_state": "standby"
                        },
                        {
                            "id": "970df1d1-505d-47a4-8d35-1b13c0dfe098",
                            "host": "cloudnet1004",
                            "admin_state_up": true,
                            "alive": ":-)",
                            "ha_state": "active"
                        }
                    ]
                """,
                "expected_agents": [
                    NeutronAgent(
                        agent_id="4be214c8-76ef-40f8-9d5d-4c344d213311",
                        host="cloudnet1003",
                        admin_state_up=True,
                        alive=True,
                        ha_state=NeutronAgentHAState.STANDBY,
                        agent_type=NeutronAgentType.L3_AGENT,
                        availability_zone=None,
                        binary="",
                    ),
                    NeutronAgent(
                        agent_id="970df1d1-505d-47a4-8d35-1b13c0dfe098",
                        host="cloudnet1004",
                        admin_state_up=True,
                        alive=True,
                        ha_state=NeutronAgentHAState.ACTIVE,
                        agent_type=NeutronAgentType.L3_AGENT,
                        availability_zone=None,
                        binary="",
                    ),
                ],
            },
        }
    )
)
def test_NeutronController_list_agents_hosting_router_works(neutron_output: str, expected_agents: list[dict[str, Any]]):
    fake_remote = UtilsForTesting.get_fake_remote(responses=[neutron_output])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    my_controller = NeutronController(openstack_api=my_api)
    fake_run_sync = fake_remote.query.return_value.run_sync

    gotten_agents = my_controller.list_agents_hosting_router(router="dummy_router")

    assert gotten_agents == expected_agents
    fake_run_sync.assert_called_with(
        cumin.transports.Command(
            "bash -c 'source /root/novaenv.sh && neutron l3-agent-list-hosting-router dummy_router --format json'",
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
            "No nodes": {
                # neutron expects a first spurious line
                "neutron_output": "\n[]",
                "expected_routers": [],
            },
            "One router": {
                "neutron_output": """
                    [
                          {
                            "id": "d93771ba-2711-4f88-804a-8df6fd03978a",
                            "name": "cloudinstances2b-gw",
                            "tenant_id": "admin",
                            "external_gateway_info": {
                            "network_id": "5c9ee953-3a19-4e84-be0f-069b5da75123",
                            "external_fixed_ips": [
                                {
                                "subnet_id": "77dba34f-c8f2-4706-a0b6-2a8ed4d91f51",
                                "ip_address": "185.15.56.238"
                                }
                            ],
                            "enable_snat": false
                            }
                        }
                    ]
                """,
                "expected_routers": [
                    {
                        "id": "d93771ba-2711-4f88-804a-8df6fd03978a",
                        "name": "cloudinstances2b-gw",
                        "tenant_id": "admin",
                        "external_gateway_info": {
                            "network_id": "5c9ee953-3a19-4e84-be0f-069b5da75123",
                            "external_fixed_ips": [
                                {"subnet_id": "77dba34f-c8f2-4706-a0b6-2a8ed4d91f51", "ip_address": "185.15.56.238"}
                            ],
                            "enable_snat": False,
                        },
                    }
                ],
            },
            "Many routers": {
                "neutron_output": """
                    [
                          {
                            "id": "d93771ba-2711-4f88-804a-8df6fd03978a",
                            "name": "cloudinstances2b-gw",
                            "tenant_id": "admin",
                            "external_gateway_info": {
                            "network_id": "5c9ee953-3a19-4e84-be0f-069b5da75123",
                            "external_fixed_ips": [
                                {
                                "subnet_id": "77dba34f-c8f2-4706-a0b6-2a8ed4d91f51",
                                "ip_address": "185.15.56.238"
                                }
                            ],
                            "enable_snat": false
                            }
                        },
                          {
                            "id": "d93771ba-2711-4f88-804a-8df6fd03978b",
                            "name": "cloudinstances2c-gw",
                            "tenant_id": "admin",
                            "external_gateway_info": {
                            "network_id": "5c9ee953-3a19-4e84-be0f-069b5da75124",
                            "external_fixed_ips": [
                                {
                                "subnet_id": "77dba34f-c8f2-4706-a0b6-2a8ed4d91f52",
                                "ip_address": "185.15.56.239"
                                }
                            ],
                            "enable_snat": false
                            }
                        }
                    ]
                """,
                "expected_routers": [
                    {
                        "id": "d93771ba-2711-4f88-804a-8df6fd03978a",
                        "name": "cloudinstances2b-gw",
                        "tenant_id": "admin",
                        "external_gateway_info": {
                            "network_id": "5c9ee953-3a19-4e84-be0f-069b5da75123",
                            "external_fixed_ips": [
                                {"subnet_id": "77dba34f-c8f2-4706-a0b6-2a8ed4d91f51", "ip_address": "185.15.56.238"}
                            ],
                            "enable_snat": False,
                        },
                    },
                    {
                        "id": "d93771ba-2711-4f88-804a-8df6fd03978b",
                        "name": "cloudinstances2c-gw",
                        "tenant_id": "admin",
                        "external_gateway_info": {
                            "network_id": "5c9ee953-3a19-4e84-be0f-069b5da75124",
                            "external_fixed_ips": [
                                {"subnet_id": "77dba34f-c8f2-4706-a0b6-2a8ed4d91f52", "ip_address": "185.15.56.239"}
                            ],
                            "enable_snat": False,
                        },
                    },
                ],
            },
        }
    )
)
def test_NeutronController_list_routers_on_agent_works(neutron_output: str, expected_routers: list[dict[str, Any]]):
    fake_remote = UtilsForTesting.get_fake_remote(responses=[neutron_output])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    my_controller = NeutronController(openstack_api=my_api)
    fake_run_sync = fake_remote.query.return_value.run_sync

    gotten_nodes = my_controller.list_routers_on_agent(agent_id="some-agent-id")

    assert gotten_nodes == expected_routers
    fake_run_sync.assert_called_with(
        cumin.transports.Command(
            "bash -c 'source /root/novaenv.sh && neutron router-list-on-l3-agent some-agent-id --format json'",
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
            "No cloudnets": {
                # neutron expects a first spurious line
                "neutron_output": "\n[]",
                "expected_cloudnets": [],
            },
            "Linux bridge agent": {
                "neutron_output": """
                    [
                        {
                            "ID": "29547916-33cd-45d8-b33c-4947921ba728",
                            "Agent Type": "Linux bridge agent",
                            "Host": "cloudnet1005",
                            "Availability Zone": null,
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-linuxbridge-agent"
                        },
                        {
                            "ID": "fe76faf1-f9f4-4d27-ba31-345441e7b655",
                            "Agent Type": "Linux bridge agent",
                            "Host": "cloudvirt1056",
                            "Availability Zone": null,
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-linuxbridge-agent"
                        }
                    ]
                """,
                "expected_cloudnets": [],
            },
            "OVS agent": {
                "neutron_output": """
                    [
                        {
                            "ID": "ad1f63bc-8acb-4d2f-a07c-13d8f8c1c7bb",
                            "Agent Type": "Open vSwitch agent",
                            "Host": "cloudnet2005-dev",
                            "Availability Zone": null,
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-openvswitch-agent"
                        }
                    ]
                """,
                "expected_cloudnets": [],
            },
            "Metadata agent": {
                "neutron_output": """
                    [
                        {
                            "ID": "97b30d69-fd14-4061-a7df-601186626a3c",
                            "Agent Type": "Metadata agent",
                            "Host": "cloudnet1006",
                            "Availability Zone": null,
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-metadata-agent"
                        }
                    ]
                """,
                "expected_cloudnets": [],
            },
            "DHCP agent": {
                "neutron_output": """
                    [
                        {
                            "ID": "e4f71e5d-e182-487d-8c5f-eb15f1ff2bf6",
                            "Agent Type": "DHCP agent",
                            "Host": "cloudnet1006",
                            "Availability Zone": "nova",
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-dhcp-agent"
                        }
                    ]
                """,
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
            "env OS_PROJECT_ID=admin-monitoring wmcs-openstack network agent list -f json --os-cloud novaadmin",
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
    agents: list[NeutronAgent], routers: list[NeutronRouter]
):
    # just in case a call gets through
    fake_remote = UtilsForTesting.get_fake_remote(responses=[])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    my_controller = NeutronController(openstack_api=my_api)
    partial_routers = [partial_router_from_full_router(router) for router in routers]

    with patch.object(my_api, "get_neutron_agents", MagicMock(return_value=agents)), patch.object(
        my_controller, "router_list", MagicMock(return_value=partial_routers)
    ), patch.object(my_controller, "router_show", MagicMock(side_effect=routers)):
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
def test_NeutronController_check_if_network_is_alive_raises(agents: list[NeutronAgent], routers: list[NeutronRouter]):
    # just in case a call gets through
    fake_remote = UtilsForTesting.get_fake_remote(responses=[])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    my_controller = NeutronController(openstack_api=my_api)
    partial_routers = [partial_router_from_full_router(router) for router in routers]

    with patch.object(my_api, "get_neutron_agents", MagicMock(return_value=agents)), patch.object(
        my_controller, "router_list", MagicMock(return_value=partial_routers)
    ), patch.object(my_controller, "router_show", MagicMock(side_effect=routers)):
        with pytest.raises(NetworkUnhealthy):
            my_controller.check_if_network_is_alive()
