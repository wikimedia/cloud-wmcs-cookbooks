from __future__ import annotations

from dataclasses import asdict
from unittest import mock

import cumin
import pytest

from wmcs_libs.common import CUMIN_SAFE_WITHOUT_OUTPUT, UtilsForTesting
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import (
    NeutronAgentHAState,
    NeutronAgentType,
    NeutronAgentWithHAState,
    NeutronPartialAgent,
    NeutronPartialRouter,
    NeutronRouterStatus,
    OpenstackAPI,
    OpenstackBadQuota,
    OpenstackQuotaEntry,
    OpenstackQuotaName,
    Unit,
)


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "G to M": {
                "from_unit": Unit.GIGA,
                "expected_unit": Unit.MEGA,
            },
            "M to K": {
                "from_unit": Unit.MEGA,
                "expected_unit": Unit.KILO,
            },
            "K to B": {
                "from_unit": Unit.KILO,
                "expected_unit": Unit.UNIT,
            },
        }
    )
)
def test_Unit_next_unit_works(from_unit: Unit, expected_unit: Unit):
    gotten_unit = from_unit.next_unit()
    assert gotten_unit == expected_unit


def test_Unit_next_unit_raises_when_last_unit():
    with pytest.raises(OpenstackBadQuota):
        Unit.UNIT.next_unit()


# Test only a couple very used ones
@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "Gigabytes": {
                "quota_name": OpenstackQuotaName.GIGABYTES,
                "value": "3G",
                "expected_cli": "--gigabytes=3",
            },
            "Per-volume gigabytes": {
                "quota_name": OpenstackQuotaName.PER_VOLUME_GIGABYTES,
                "value": "4G",
                "expected_cli": "--per-volume-gigabytes=4",
            },
            "Cores": {
                "quota_name": OpenstackQuotaName.CORES,
                "value": "15",
                "expected_cli": "--cores=15",
            },
        }
    )
)
def test_OpenstackQuotaEntry_name_to_cli_works(quota_name: OpenstackQuotaName, value: str, expected_cli: str):
    gotten_cli = OpenstackQuotaEntry.from_human_spec(name=quota_name, human_spec=value).to_cli()
    assert gotten_cli == expected_cli


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "Gigabytes passing 10G": {
                "human_str": "10G",
                "quota_name": OpenstackQuotaName.GIGABYTES,
                "expected_value": 10,
            },
            "Gigabytes passing 10": {
                "human_str": "10",
                "quota_name": OpenstackQuotaName.GIGABYTES,
                "expected_value": 10,
            },
            "CORES passing 20": {
                "human_str": "20",
                "quota_name": OpenstackQuotaName.CORES,
                "expected_value": 20,
            },
            "RAM passing 20": {
                "human_str": "20",
                "quota_name": OpenstackQuotaName.RAM,
                "expected_value": 20,
            },
            "RAM passing 20M": {
                "human_str": "20M",
                "quota_name": OpenstackQuotaName.RAM,
                "expected_value": 20,
            },
            "RAM passing 20G": {
                "human_str": "20G",
                "quota_name": OpenstackQuotaName.RAM,
                "expected_value": 20 * 1024,
            },
        }
    )
)
def test_OpenstackQuotaEntry___init__works(human_str: str, quota_name: OpenstackQuotaName, expected_value: str):
    gotten_entry = OpenstackQuotaEntry.from_human_spec(human_spec=human_str, name=quota_name)
    assert gotten_entry.value == expected_value


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "Gigabytes passing 10K": {
                "human_str": "10K",
                "quota_name": OpenstackQuotaName.GIGABYTES,
            },
            "Gigabytes passing 10M": {
                "human_str": "10M",
                "quota_name": OpenstackQuotaName.GIGABYTES,
            },
            "RAM passing 20K": {
                "human_str": "20K",
                "quota_name": OpenstackQuotaName.RAM,
            },
        }
    )
)
def test_OpenstackQuotaEntry___init__raises(human_str: str, quota_name: OpenstackQuotaName):
    with pytest.raises(OpenstackBadQuota):
        OpenstackQuotaEntry.from_human_spec(human_spec=human_str, name=quota_name)


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "10G RAM + 200M RAM": {
                "quota_name": OpenstackQuotaName.RAM,
                "human_spec1": "10G",
                "human_spec2": "100M",
                "expected_sum": 10340,
            },
            "10G RAM + 200G RAM": {
                "quota_name": OpenstackQuotaName.RAM,
                "human_spec1": "10G",
                "human_spec2": "100G",
                "expected_sum": 10 * 1024 + 100 * 1024,
            },
            "10 RAM + 200G RAM": {
                "quota_name": OpenstackQuotaName.RAM,
                "human_spec1": "10",
                "human_spec2": "100G",
                "expected_sum": 10 + 100 * 1024,
            },
            "10 CORES + 200 CORES": {
                "quota_name": OpenstackQuotaName.CORES,
                "human_spec1": "10",
                "human_spec2": "100",
                "expected_sum": 110,
            },
            "10 Gigabytes + 200G Gigabytes": {
                "quota_name": OpenstackQuotaName.GIGABYTES,
                "human_spec1": "10",
                "human_spec2": "200G",
                "expected_sum": 210,
            },
        }
    )
)
def test_summing_up_two_quota_entries(
    quota_name: OpenstackQuotaName, human_spec1: str, human_spec2: str, expected_sum: int
):
    entry1 = OpenstackQuotaEntry.from_human_spec(name=quota_name, human_spec=human_spec1)
    entry2 = OpenstackQuotaEntry.from_human_spec(name=quota_name, human_spec=human_spec2)
    assert int(entry1.value) + int(entry2.value) == expected_sum


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "No cloudnets": {
                "neutron_output": "[]",
                "expected_agents": [],
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
                "expected_agents": [
                    NeutronPartialAgent(
                        agent_id="ad1f63bc-8acb-4d2f-a07c-13d8f8c1c7bb",
                        agent_type=NeutronAgentType.OVS_AGENT,
                        host="cloudnet2005-dev",
                        availability_zone=None,
                        alive=True,
                        admin_state_up=True,
                        binary="neutron-openvswitch-agent",
                    ),
                ],
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
                "expected_agents": [
                    NeutronPartialAgent(
                        agent_id="97b30d69-fd14-4061-a7df-601186626a3c",
                        agent_type=NeutronAgentType.METADATA_AGENT,
                        host="cloudnet1006",
                        availability_zone=None,
                        alive=True,
                        admin_state_up=True,
                        binary="neutron-metadata-agent",
                    ),
                ],
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
                "expected_agents": [
                    NeutronPartialAgent(
                        agent_id="e4f71e5d-e182-487d-8c5f-eb15f1ff2bf6",
                        agent_type=NeutronAgentType.DHCP_AGENT,
                        host="cloudnet1006",
                        availability_zone="nova",
                        alive=True,
                        admin_state_up=True,
                        binary="neutron-dhcp-agent",
                    ),
                ],
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
                "expected_agents": [
                    NeutronPartialAgent(
                        agent_id="3f54b3c2-503f-4667-8263-859a259b3b21",
                        agent_type=NeutronAgentType.L3_AGENT,
                        host="cloudnet1006",
                        availability_zone="nova",
                        alive=True,
                        admin_state_up=True,
                        binary="neutron-l3-agent",
                    ),
                ],
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
                "expected_agents": [
                    NeutronPartialAgent(
                        agent_id="6a88c860-29fb-4a85-8aea-6a8877c2e035",
                        agent_type=NeutronAgentType.L3_AGENT,
                        host="cloudnet1005",
                        availability_zone="nova",
                        alive=True,
                        admin_state_up=True,
                        binary="neutron-l3-agent",
                    ),
                    NeutronPartialAgent(
                        agent_id="3f54b3c2-503f-4667-8263-859a259b3b21",
                        agent_type=NeutronAgentType.L3_AGENT,
                        host="cloudnet1006",
                        availability_zone="nova",
                        alive=True,
                        admin_state_up=True,
                        binary="neutron-l3-agent",
                    ),
                ],
            },
        }
    )
)
def test_OpenstackAPI_get_neutron_agents_works(neutron_output: str, expected_agents: list[NeutronPartialAgent]):
    fake_remote = UtilsForTesting.get_fake_remote(responses=[neutron_output])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    fake_run_sync = fake_remote.query.return_value.run_sync

    gotten_agents = my_api.get_neutron_agents()
    assert gotten_agents == expected_agents

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
            "No nodes": {
                # neutron expects a first spurious line
                "neutron_output": "\n[]",
                "expected_agents": [],
            },
            "One node": {
                "neutron_output": """
                    [
                        {
                            "ID": "3f54b3c2-503f-4667-8263-859a259b3b21",
                            "Agent Type": "L3 agent",
                            "Host": "cloudnet1006",
                            "Availability Zone": "nova",
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-l3-agent",
                            "HA State": "standby"
                        }
                    ]
                """,
                "expected_agents": [
                    NeutronAgentWithHAState(
                        agent_type=NeutronAgentType.L3_AGENT,
                        agent_id="3f54b3c2-503f-4667-8263-859a259b3b21",
                        host="cloudnet1006",
                        admin_state_up=True,
                        alive=True,
                        ha_state=NeutronAgentHAState.STANDBY,
                        availability_zone="nova",
                        binary="neutron-l3-agent",
                    ),
                ],
            },
            "Many nodes": {
                "neutron_output": """
                    [
                        {
                            "ID": "3f54b3c2-503f-4667-8263-859a259b3b21",
                            "Agent Type": "L3 agent",
                            "Host": "cloudnet1006",
                            "Availability Zone": "nova",
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-l3-agent",
                            "HA State": "standby"
                        },
                        {
                            "ID": "6a88c860-29fb-4a85-8aea-6a8877c2e035",
                            "Agent Type": "L3 agent",
                            "Host": "cloudnet1005",
                            "Availability Zone": "nova",
                            "Alive": true,
                            "State": true,
                            "Binary": "neutron-l3-agent",
                            "HA State": "active"
                        }
                    ]
                """,
                "expected_agents": [
                    NeutronAgentWithHAState(
                        agent_id="3f54b3c2-503f-4667-8263-859a259b3b21",
                        host="cloudnet1006",
                        admin_state_up=True,
                        alive=True,
                        ha_state=NeutronAgentHAState.STANDBY,
                        agent_type=NeutronAgentType.L3_AGENT,
                        availability_zone="nova",
                        binary="neutron-l3-agent",
                    ),
                    NeutronAgentWithHAState(
                        agent_id="6a88c860-29fb-4a85-8aea-6a8877c2e035",
                        host="cloudnet1005",
                        admin_state_up=True,
                        alive=True,
                        ha_state=NeutronAgentHAState.ACTIVE,
                        agent_type=NeutronAgentType.L3_AGENT,
                        availability_zone="nova",
                        binary="neutron-l3-agent",
                    ),
                ],
            },
        }
    )
)
def test_NeutronController_list_agents_hosting_router_works(
    neutron_output: str, expected_agents: list[dict[str, NeutronAgentWithHAState]]
):
    fake_remote = UtilsForTesting.get_fake_remote(responses=[neutron_output])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    fake_run_sync = fake_remote.query.return_value.run_sync

    gotten_agents = my_api.get_neutron_agents_for_router(router_id="dummy_router")

    assert gotten_agents == expected_agents
    fake_run_sync.assert_called_with(
        cumin.transports.Command(
            "env OS_PROJECT_ID=admin-monitoring wmcs-openstack network agent list --long --router=dummy_router -f json --os-cloud novaadmin",  # noqa: E501
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
            "No routers": {
                # neutron expects a first line that will be discarded
                "neutron_output": "\n[]",
                "expected_routers": [],
            },
            "One router": {
                "neutron_output": """
                    [
                        {
                            "ID": "5712e22e-134a-40d3-a75a-1c9b441717ad",
                            "Name": "cloudinstances2b-gw",
                            "Status": "ACTIVE",
                            "State": true,
                            "Project": "admin",
                            "Distributed": false,
                            "HA": true
                        }
                    ]
                """,
                "expected_routers": [
                    NeutronPartialRouter(
                        router_id="5712e22e-134a-40d3-a75a-1c9b441717ad",
                        name="cloudinstances2b-gw",
                        tenant_id="admin",
                        has_ha=True,
                        admin_state_up=True,
                        status=NeutronRouterStatus.ACTIVE,
                    ),
                ],
            },
            "Many routers": {
                "neutron_output": """
                    [
                        {
                            "ID": "4663da98-bd3d-4f5b-a91d-afc23ca507f6",
                            "Name": "cloudinstances3-flat-gw",
                            "Status": "ACTIVE",
                            "State": true,
                            "Project": "admin",
                            "Distributed": false,
                            "HA": true
                        },
                        {
                            "ID": "5712e22e-134a-40d3-a75a-1c9b441717ad",
                            "Name": "cloudinstances2b-gw",
                            "Status": "ACTIVE",
                            "State": true,
                            "Project": "admin",
                            "Distributed": false,
                            "HA": true
                        }
                    ]
                """,
                "expected_routers": [
                    NeutronPartialRouter(
                        router_id="4663da98-bd3d-4f5b-a91d-afc23ca507f6",
                        name="cloudinstances3-flat-gw",
                        tenant_id="admin",
                        has_ha=True,
                        admin_state_up=True,
                        status=NeutronRouterStatus.ACTIVE,
                    ),
                    NeutronPartialRouter(
                        router_id="5712e22e-134a-40d3-a75a-1c9b441717ad",
                        name="cloudinstances2b-gw",
                        tenant_id="admin",
                        has_ha=True,
                        admin_state_up=True,
                        status=NeutronRouterStatus.ACTIVE,
                    ),
                ],
            },
        }
    )
)
def test_NeutronController_router_list_works(neutron_output: str, expected_routers: list[NeutronPartialAgent]):
    fake_remote = UtilsForTesting.get_fake_remote(responses=[neutron_output])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.EQIAD1)
    fake_run_sync = fake_remote.query.return_value.run_sync

    gotten_routers = my_api.get_routers()

    assert gotten_routers == expected_routers
    fake_run_sync.assert_called_with(
        cumin.transports.Command(
            "env OS_PROJECT_ID=admin-monitoring wmcs-openstack router list -f json --os-cloud novaadmin",
            ok_codes=[0],
        ),
        is_safe=True,
        print_output=False,
        print_progress_bars=False,
        success_threshold=1,
        batch_size=None,
        batch_sleep=None,
    )


def test_OpenstackAPI_quota_show_happy_path():
    fake_remote = UtilsForTesting.get_fake_remote(
        # openstack quota show -f json admin-monitoring
        responses=[
            """[
                {
                    "Resource": "cores",
                    "Limit": 26
                },
                {
                    "Resource": "instances",
                    "Limit": 3
                },
                {
                    "Resource": "ram",
                    "Limit": 16416
                },
                {
                    "Resource": "volumes",
                    "Limit": 8
                },
                {
                    "Resource": "snapshots",
                    "Limit": 16
                },
                {
                    "Resource": "gigabytes",
                    "Limit": 80
                },
                {
                    "Resource": "backups",
                    "Limit": 1000
                }
            ]"""
        ]
    )
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.CODFW1DEV)
    gotten_quotas = my_api.quota_show()

    fake_remote.query.assert_called_once()
    fake_remote.query.return_value.run_sync.assert_called_once()

    assert OpenstackQuotaName.GIGABYTES in gotten_quotas
    assert gotten_quotas[OpenstackQuotaName.GIGABYTES] == OpenstackQuotaEntry(
        name=OpenstackQuotaName.GIGABYTES, value=80
    )


def test_OpenstackAPI_quota_set_happy_path():
    fake_remote = UtilsForTesting.get_fake_remote(responses=[""])
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.CODFW1DEV)
    my_api.quota_set(
        OpenstackQuotaEntry(name=OpenstackQuotaName.CORES, value=10),
        OpenstackQuotaEntry(name=OpenstackQuotaName.GIGABYTES, value=20),
        OpenstackQuotaEntry(name=OpenstackQuotaName.FLOATING_IPS, value=30),
    )
    expected_command = cumin.transports.Command(
        ("wmcs-openstack quota set --cores=10 --gigabytes=20 --floating-ips=30 admin-monitoring --os-cloud novaadmin"),
        ok_codes=[0],
    )
    fake_control_host = fake_remote.query.return_value
    fake_control_host.run_sync.assert_called_with(expected_command)


def test_OpenstackAPI_quota_increase_happy_path():
    fake_remote = UtilsForTesting.get_fake_remote(
        # openstack quota show -f json admin-monitoring
        responses=[
            # First show to see what's there
            """[
                {
                    "Resource": "floating-ips",
                    "Limit": 0
                },
                {
                    "Resource": "cores",
                    "Limit": 1
                },
                    {
                    "Resource": "gigabytes",
                    "Limit": 1
                }
            ]""",
            # quota set response
            "",
            # last show to verify the increases were made
            """[
                {
                    "Resource": "floating-ips",
                    "Limit": 30
                },
                {
                    "Resource": "cores",
                    "Limit": 11
                },
                {
                    "Resource": "gigabytes",
                    "Limit": 21
                }
            ]""",
        ]
    )
    my_api = OpenstackAPI(remote=fake_remote, project="admin-monitoring", cluster_name=OpenstackClusterName.CODFW1DEV)
    my_api.quota_increase(
        OpenstackQuotaEntry(name=OpenstackQuotaName.CORES, value=10),
        OpenstackQuotaEntry(name=OpenstackQuotaName.GIGABYTES, value=20),
        OpenstackQuotaEntry(name=OpenstackQuotaName.FLOATING_IPS, value=30),
    )

    expected_show_command = cumin.transports.Command(
        ("wmcs-openstack quota show admin-monitoring -f json --os-cloud novaadmin"),
        ok_codes=[0],
    )

    expected_set_command = cumin.transports.Command(
        ("wmcs-openstack quota set --cores=11 --gigabytes=21 --floating-ips=30 admin-monitoring --os-cloud novaadmin"),
        ok_codes=[0],
    )

    fake_control_host = fake_remote.query.return_value
    assert fake_control_host.run_sync.call_count == 3
    calls = [
        mock.call(expected_show_command, **asdict(CUMIN_SAFE_WITHOUT_OUTPUT)),
        mock.call(expected_set_command),
        mock.call(expected_show_command, **asdict(CUMIN_SAFE_WITHOUT_OUTPUT)),
    ]
    fake_control_host.run_sync.assert_has_calls(calls)
