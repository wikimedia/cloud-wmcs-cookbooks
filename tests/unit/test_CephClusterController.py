from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Type
from unittest import mock

import pytest
from cumin.transports import Command
from freezegun import freeze_time
from spicerack import Spicerack

from wmcs_libs.ceph import (
    CephClusterController,
    CephClusterUnhealthy,
    CephFlagSetError,
    CephNoControllerNode,
    CephOSDFlag,
    CephTestUtils,
    CephTimeout,
    OSDClass,
    OSDStatus,
    OSDTree,
    OSDTreeNode,
    OSDTreeOSDNode,
)
from wmcs_libs.inventory.ceph import CephClusterName


def parametrize(params: dict[str, Any]):
    def decorator(decorated):
        return pytest.mark.parametrize(**CephTestUtils.to_parametrize(params))(decorated)

    return decorator


@parametrize(
    {
        "When there's no nodes, returns empty dict.": {
            "expected_nodes": {},
            "nodes_command_output": "{}",
        },
        "When there's some output (single line), returns the correct dict.": {
            "expected_nodes": {
                "mon": {"monhost1": ["mon1"], "monhost2": ["mon2"]},
                "osd": {"osdhost1": [0, 1], "osdhost2": [2, 3]},
                "mgr": {"mgrhost1": ["mgr1"], "mgrhost2": ["mgr2"]},
            },
            "nodes_command_output": (
                '{"mon":{"monhost1":["mon1"],"monhost2":["mon2"]}, "osd":{"osdhost1":[0,1],"osdhost2":[2,3]}, '
                '"mgr":{"mgrhost1":["mgr1"],"mgrhost2":["mgr2"]}}'
            ),
        },
        "When there's some output (and multiple lines), parses only the last line.": {
            "expected_nodes": {
                "mon": {"monhost1": ["mon1"], "monhost2": ["mon2"]},
                "osd": {"osdhost1": [0, 1], "osdhost2": [2, 3]},
                "mgr": {"mgrhost1": ["mgr1"], "mgrhost2": ["mgr2"]},
            },
            "nodes_command_output": "\n".join(
                [
                    "Some extra output",
                    (
                        '{"mon":{"monhost1":["mon1"],"monhost2":["mon2"]}, "osd":{"osdhost1":[0,1],"osdhost2":[2,3]},'
                        ' "mgr":{"mgrhost1":["mgr1"],"mgrhost2":["mgr2"]}}'
                    ),
                ]
            ),
        },
    }
)
def test_get_nodes_happy_path(expected_nodes: list[str], nodes_command_output: str):
    fake_remote = CephTestUtils.get_fake_remote(responses=[nodes_command_output])
    my_controller = CephClusterController(
        remote=fake_remote,
        cluster_name=CephClusterName.EQIAD1,
        spicerack=CephTestUtils.get_fake_spicerack(fake_remote=fake_remote),
    )

    gotten_nodes = my_controller.get_nodes()

    assert gotten_nodes == expected_nodes


@parametrize(
    {
        "When there's only one other node, returns the other node.": {
            "expected_controlling_node": "monhost2.eqiad.wmnet",
            "nodes_command_output": '{"mon":{"cloudcephmon1001":["mon1"],"monhost2":["mon2"]}}',
        },
    },
)
def test_change_controlling_node_happy_path(expected_controlling_node: str, nodes_command_output: str):
    fake_remote = CephTestUtils.get_fake_remote(responses=[nodes_command_output])
    my_controller = CephClusterController(
        remote=fake_remote,
        cluster_name=CephClusterName.EQIAD1,
        spicerack=CephTestUtils.get_fake_spicerack(fake_remote=fake_remote),
    )

    my_controller.change_controlling_node()

    assert my_controller.controlling_node_fqdn == expected_controlling_node


@parametrize(
    {
        "When there's no other nodes it raises CephNoControllerNode": {
            "nodes_command_output": '{"mon":{"cloudcephmon1001":["mon1"]}}'
        },
    },
)
def test_change_controlling_node_raising(nodes_command_output: str):
    fake_remote = CephTestUtils.get_fake_remote(responses=[nodes_command_output])
    my_controller = CephClusterController(
        remote=fake_remote,
        cluster_name=CephClusterName.EQIAD1,
        spicerack=CephTestUtils.get_fake_spicerack(fake_remote=fake_remote),
    )

    with pytest.raises(CephNoControllerNode):
        my_controller.change_controlling_node()


@parametrize(
    {
        "It generates a status with the correct status dict.": {
            "status_command_output": json.dumps(CephTestUtils.get_status_dict()),
            "expected_status_dict": CephTestUtils.get_status_dict(),
        },
    },
)
def test_get_cluster_status_happy_path(status_command_output: str, expected_status_dict: dict[str, Any]):
    fake_remote = CephTestUtils.get_fake_remote(responses=[status_command_output])
    my_controller = CephClusterController(
        remote=fake_remote,
        cluster_name=CephClusterName.EQIAD1,
        spicerack=CephTestUtils.get_fake_spicerack(fake_remote=fake_remote),
    )

    my_status = my_controller.get_cluster_status()

    assert my_status.status_dict == expected_status_dict


@parametrize(
    {
        "Passes if flag was set (output has the correct format)": {
            "set_flag_command_output": f"{CephOSDFlag.NOREBALANCE.value} is set",
        },
        "Passes if flag was set (output has the correct format with newlines)": {
            "set_flag_command_output": f"\n{CephOSDFlag.NOREBALANCE.value} is set\n",
        },
    },
)
def test_set_osdmap_flag_happy_path(set_flag_command_output: str):
    fake_remote = CephTestUtils.get_fake_remote(responses=[set_flag_command_output])
    my_controller = CephClusterController(
        remote=fake_remote,
        cluster_name=CephClusterName.EQIAD1,
        spicerack=CephTestUtils.get_fake_spicerack(fake_remote=fake_remote),
    )

    my_controller.set_osdmap_flag(flag=CephOSDFlag.NOREBALANCE)

    my_controller._controlling_node.run_sync.assert_called_with(
        Command(f"ceph osd set {CephOSDFlag.NOREBALANCE.value}", ok_codes=[0])
    )


@parametrize(
    {
        "Raises CephFlagSetError if the set command does not return the correct output": {
            "set_flag_command_output": f"some error happened when setting {CephOSDFlag.NOREBALANCE.value}",
        },
    },
)
def test_set_osdmap_flag_raising(set_flag_command_output: str):
    fake_remote = CephTestUtils.get_fake_remote(responses=[set_flag_command_output])
    my_controller = CephClusterController(
        remote=fake_remote,
        cluster_name=CephClusterName.EQIAD1,
        spicerack=CephTestUtils.get_fake_spicerack(fake_remote=fake_remote),
    )

    with pytest.raises(CephFlagSetError):
        my_controller.set_osdmap_flag(flag=CephOSDFlag.NOREBALANCE)

    my_controller._controlling_node.run_sync.assert_called_with(
        Command(f"ceph osd set {CephOSDFlag.NOREBALANCE.value}", ok_codes=[0])
    )


@parametrize(
    {
        "Passes if flag was unset (output has the correct format)": {
            "unset_flag_command_output": f"{CephOSDFlag.NOREBALANCE.value} is unset",
        },
        "Passes if flag was unset (output has the correct format, multiline)": {
            "unset_flag_command_output": f"{CephOSDFlag.NOREBALANCE.value} is unset",
        },
    },
)
def test_unset_osdmap_flag_happy_path(unset_flag_command_output: str):
    fake_remote = CephTestUtils.get_fake_remote(responses=[unset_flag_command_output])
    my_controller = CephClusterController(
        remote=fake_remote,
        cluster_name=CephClusterName.EQIAD1,
        spicerack=CephTestUtils.get_fake_spicerack(fake_remote=fake_remote),
    )

    my_controller.unset_osdmap_flag(flag=CephOSDFlag.NOREBALANCE)

    my_controller._controlling_node.run_sync.assert_called_with(
        Command(f"ceph osd unset {CephOSDFlag.NOREBALANCE.value}", ok_codes=[0])
    )


@parametrize(
    {
        "Raises CephFlagSetError if the unset command does not return the correct output": {
            "unset_flag_command_output": f"some error happened when unsetting {CephOSDFlag.NOREBALANCE.value}",
        },
    },
)
def test_unset_osdmap_flag_raising(unset_flag_command_output: str):
    fake_remote = CephTestUtils.get_fake_remote(responses=[unset_flag_command_output])
    my_controller = CephClusterController(
        remote=fake_remote,
        cluster_name=CephClusterName.EQIAD1,
        spicerack=CephTestUtils.get_fake_spicerack(fake_remote=fake_remote),
    )

    with pytest.raises(CephFlagSetError):
        my_controller.unset_osdmap_flag(flag=CephOSDFlag.NOREBALANCE)

    my_controller._controlling_node.run_sync.assert_called_with(
        Command(f"ceph osd unset {CephOSDFlag.NOREBALANCE.value}", ok_codes=[0])
    )


@parametrize(
    {
        "Does nothing if cluster already in maintenance": {
            "commands_output": [
                json.dumps(CephTestUtils.get_maintenance_status_dict()),
                "noout should not try to be set",
                "norebalance should not try to be set",
            ],
        },
        "Passes if cluster healthy": {
            "commands_output": [
                json.dumps(CephTestUtils.get_ok_status_dict()),
                "noout is set",
                "norebalance is set",
            ],
        },
        "Passes if cluster not healthy but force is True": {
            "commands_output": [
                json.dumps(CephTestUtils.get_warn_status_dict()),
                "noout is set",
                "norebalance is set",
            ],
            "force": True,
        },
    },
)
def test_set_maintenance_happy_path(commands_output: list[str], force: bool | None):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(responses=commands_output),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    my_controller.set_maintenance(force=bool(force), reason="Doing some tests")


@parametrize(
    {
        "Raises if cluster unhealthy and not force": {
            "commands_output": [
                json.dumps(CephTestUtils.get_warn_status_dict()),
                "noout should not try to be set",
                "norebalance should not try to be set",
            ],
            "force": False,
            "exception": CephClusterUnhealthy,
        },
        "Raises if it failed to set noout": {
            "commands_output": [
                json.dumps(CephTestUtils.get_ok_status_dict()),
                "noout is not set",
                "norebalance is set",
            ],
            "exception": CephFlagSetError,
        },
        "Raises if it failed to set norebalance": {
            "commands_output": [
                json.dumps(CephTestUtils.get_ok_status_dict()),
                "noout is set",
                "norebalance is not set",
            ],
            "exception": CephFlagSetError,
        },
    },
)
def test_set_maintenance_raising(commands_output: list[str], exception: Type[Exception], force: bool | None):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(responses=commands_output),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    with pytest.raises(exception):
        my_controller.set_maintenance(force=bool(force), reason="Doing tests")


@parametrize(
    {
        "Passes if cluster in maintenance": {
            "commands_output": [
                json.dumps(CephTestUtils.get_maintenance_status_dict()),
                "noout is unset",
                "norebalance is unset",
            ]
            + [json.dumps([])] * len(CephClusterController.CLUSTER_ALERT_MATCH),
        },
        "Passes if cluster not healthy but force is True": {
            "commands_output": [
                json.dumps(CephTestUtils.get_warn_status_dict()),
                "noout is unset",
                "norebalance is unset",
            ]
            + [json.dumps([])] * len(CephClusterController.CLUSTER_ALERT_MATCH),
            "force": True,
        },
    },
)
def test_unset_maintenance_happy_path(commands_output: list[str], force: bool | None):
    fake_remote = CephTestUtils.get_fake_remote(responses=commands_output)
    my_controller = CephClusterController(
        remote=fake_remote,
        cluster_name=CephClusterName.EQIAD1,
        spicerack=CephTestUtils.get_fake_spicerack(fake_remote=fake_remote),
    )

    my_controller.unset_maintenance(force=bool(force))


@parametrize(
    {
        "Raises if cluster unhealthy and not force": {
            "commands_output": [json.dumps(CephTestUtils.get_warn_status_dict())],
            "force": False,
            "exception": CephClusterUnhealthy,
        },
        "Raises if cluster only maintenance and it failed to unset noout": {
            "commands_output": [
                json.dumps(CephTestUtils.get_maintenance_status_dict()),
                "noout is set",
                "norebalance is not set",
            ],
            "exception": CephFlagSetError,
        },
        "Raises if it failed to unset norebalance": {
            "commands_output": [
                json.dumps(CephTestUtils.get_maintenance_status_dict()),
                "noout is not set",
                "norebalance is set",
            ],
            "exception": CephFlagSetError,
        },
    },
)
def test_unset_maintenance_raising(commands_output: list[str], exception: Type[Exception], force: bool | None):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(responses=commands_output),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    with pytest.raises(exception):
        my_controller.unset_maintenance(force=bool(force))


@parametrize(
    {
        "Passes if no in-progress events": {
            "commands_output": [json.dumps(CephTestUtils.get_status_dict({"progress_events": {}}))],
            "auto_tick_seconds": 0,
        },
        "Passes if in-progress events get resolved before timeout": {
            "commands_output": [
                json.dumps(CephTestUtils.get_status_dict({"progress_events": {"some event": {"progress": 0}}})),
                json.dumps(CephTestUtils.get_status_dict({"progress_events": {}})),
            ],
            "auto_tick_seconds": 1,
            "timeout": timedelta(seconds=100),
        },
    }
)
def test_wait_for_progress_events_happy_path(
    commands_output: list[str],
    auto_tick_seconds: int,
    timeout: timedelta | None,
):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(responses=commands_output),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    with freeze_time(auto_tick_seconds=auto_tick_seconds), mock.patch("wmcs_libs.ceph.time.sleep"):
        if timeout is not None:
            my_controller.wait_for_in_progress_events(timeout=timeout)
        else:
            my_controller.wait_for_in_progress_events()


@parametrize(
    {
        "Raises if timeout reached before no in-progress events": {
            "commands_output": [
                json.dumps(CephTestUtils.get_status_dict({"progress_events": {"some event": {"progress": 0}}})),
                json.dumps(CephTestUtils.get_status_dict({"progress_events": {"some event": {"progress": 0}}})),
            ],
            "auto_tick_seconds": 101,
            "timeout": timedelta(seconds=100),
        },
    }
)
def test_wait_for_progress_events_raises(
    commands_output: list[str],
    auto_tick_seconds: int,
    timeout: timedelta,
):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(responses=commands_output),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    with freeze_time(auto_tick_seconds=auto_tick_seconds), mock.patch("wmcs_libs.ceph.time.sleep"), pytest.raises(
        CephTimeout
    ):
        my_controller.wait_for_in_progress_events(timeout=timeout)


@parametrize(
    {
        "Passes if cluster healthy": {
            "commands_output": [json.dumps(CephTestUtils.get_ok_status_dict())],
            "auto_tick_seconds": 1,
        },
        "Passes if cluster in maintenance and consider_maintenance_healthy True": {
            "commands_output": [json.dumps(CephTestUtils.get_maintenance_status_dict())],
            "auto_tick_seconds": 1,
            "consider_maintenance_healthy": True,
        },
        "Passes if in-progress events get resolved before timeout": {
            "commands_output": [
                json.dumps(CephTestUtils.get_warn_status_dict()),
                json.dumps(CephTestUtils.get_ok_status_dict()),
            ],
            "auto_tick_seconds": 1,
            "timeout": timedelta(seconds=100),
        },
    }
)
def test_wait_for_cluster_health_happy_path(
    commands_output: list[str],
    auto_tick_seconds: int,
    timeout: timedelta | None,
    consider_maintenance_healthy: bool | None,
):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(responses=commands_output),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    params: dict[str, Any] = {}
    if consider_maintenance_healthy is not None:
        params["consider_maintenance_healthy"] = consider_maintenance_healthy
    if timeout is not None:
        params["timeout"] = timeout

    with freeze_time(auto_tick_seconds=auto_tick_seconds), mock.patch("wmcs_libs.ceph.time.sleep"):
        my_controller.wait_for_cluster_healthy(**params)


@parametrize(
    {
        "Raises if cluster not healthy before timeout": {
            "commands_output": [
                json.dumps(CephTestUtils.get_warn_status_dict()),
                json.dumps(CephTestUtils.get_warn_status_dict()),
            ],
            "auto_tick_seconds": 101,
            "timeout": timedelta(seconds=100),
        },
        "Raises if cluster in maintenance and consider_maintenance_healthy is False": {
            "commands_output": [
                json.dumps(CephTestUtils.get_warn_status_dict()),
                json.dumps(CephTestUtils.get_warn_status_dict()),
            ],
            "auto_tick_seconds": 101,
            "timeout": timedelta(seconds=100),
            "consider_maintenance_healthy": True,
        },
    }
)
def test_wait_for_cluster_health_raises(
    commands_output: list[str],
    auto_tick_seconds: int,
    timeout: timedelta,
    consider_maintenance_healthy: bool | None,
):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(responses=commands_output),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    params: dict[str, Any] = {"timeout": timeout}
    if consider_maintenance_healthy is not None:
        params["consider_maintenance_healthy"] = consider_maintenance_healthy

    with freeze_time(auto_tick_seconds=auto_tick_seconds), mock.patch("wmcs_libs.ceph.time.sleep"), pytest.raises(
        CephClusterUnhealthy
    ):
        my_controller.wait_for_cluster_healthy(**params)


@parametrize(
    {
        "Parse the OSD tree returned by 'ceph osd tree' with rack levels": {
            # root@cloudcephmon2004-dev:~# ceph osd tree -f json
            "osd_tree_command_output": """
            {
                "nodes": [
                    {
                        "id": -1,
                        "name": "default",
                        "type": "root",
                        "type_id": 11,
                        "children": [
                            -11,
                            -9,
                            -13
                        ]
                    },
                    {
                        "id": -13,
                        "name": "C8D5",
                        "type": "rack",
                        "type_id": 3,
                        "pool_weights": {},
                        "children": [
                            -3
                        ]
                    },
                    {
                        "id": -3,
                        "name": "cloudcephosd2001-dev",
                        "type": "host",
                        "type_id": 1,
                        "pool_weights": {},
                        "children": [
                            1,
                            0
                        ]
                    },
                    {
                        "id": 0,
                        "device_class": "ssd",
                        "name": "osd.0",
                        "type": "osd",
                        "type_id": 0,
                        "crush_weight": 0.87298583984375,
                        "depth": 3,
                        "pool_weights": {},
                        "exists": 1,
                        "status": "up",
                        "reweight": 1,
                        "primary_affinity": 1
                    },
                    {
                        "id": 1,
                        "device_class": "ssd",
                        "name": "osd.1",
                        "type": "osd",
                        "type_id": 0,
                        "crush_weight": 0.87298583984375,
                        "depth": 3,
                        "pool_weights": {},
                        "exists": 1,
                        "status": "up",
                        "reweight": 1,
                        "primary_affinity": 1
                    },
                    {
                        "id": -9,
                        "name": "E4",
                        "type": "rack",
                        "type_id": 3,
                        "pool_weights": {},
                        "children": [
                            -5
                        ]
                    },
                    {
                        "id": -5,
                        "name": "cloudcephosd2002-dev",
                        "type": "host",
                        "type_id": 1,
                        "pool_weights": {},
                        "children": [
                            3,
                            2
                        ]
                    },
                    {
                        "id": 2,
                        "device_class": "ssd",
                        "name": "osd.2",
                        "type": "osd",
                        "type_id": 0,
                        "crush_weight": 0.87298583984375,
                        "depth": 3,
                        "pool_weights": {},
                        "exists": 1,
                        "status": "up",
                        "reweight": 1,
                        "primary_affinity": 1
                    },
                    {
                        "id": 3,
                        "device_class": "ssd",
                        "name": "osd.3",
                        "type": "osd",
                        "type_id": 0,
                        "crush_weight": 0.87298583984375,
                        "depth": 3,
                        "pool_weights": {},
                        "exists": 1,
                        "status": "up",
                        "reweight": 1,
                        "primary_affinity": 1
                    },
                    {
                        "id": -11,
                        "name": "F4",
                        "type": "rack",
                        "type_id": 3,
                        "pool_weights": {},
                        "children": [
                            -7
                        ]
                    },
                    {
                        "id": -7,
                        "name": "cloudcephosd2003-dev",
                        "type": "host",
                        "type_id": 1,
                        "pool_weights": {},
                        "children": [
                            5,
                            4
                        ]
                    },
                    {
                        "id": 4,
                        "device_class": "ssd",
                        "name": "osd.4",
                        "type": "osd",
                        "type_id": 0,
                        "crush_weight": 0.87298583984375,
                        "depth": 3,
                        "pool_weights": {},
                        "exists": 1,
                        "status": "up",
                        "reweight": 1,
                        "primary_affinity": 1
                    },
                    {
                        "id": 5,
                        "device_class": "ssd",
                        "name": "osd.5",
                        "type": "osd",
                        "type_id": 0,
                        "crush_weight": 0.87298583984375,
                        "depth": 3,
                        "pool_weights": {},
                        "exists": 1,
                        "status": "up",
                        "reweight": 1,
                        "primary_affinity": 1
                    }
                ],
                "stray": []
            }
            """,
            "expected_tree": OSDTree(
                root_node=OSDTreeNode(
                    crush_weight=5.2379150390625,  # sum of the children
                    children=[
                        OSDTreeNode(
                            crush_weight=1.7459716796875,
                            children=[
                                OSDTreeNode(
                                    crush_weight=1.7459716796875,
                                    children=[
                                        OSDTreeOSDNode(
                                            node_id=5,
                                            type="osd",
                                            children=[],
                                            osd_id=5,
                                            name="osd.5",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=0.87298583984375,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=4,
                                            type="osd",
                                            children=[],
                                            osd_id=4,
                                            name="osd.4",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=0.87298583984375,
                                        ),
                                    ],
                                    node_id=-7,
                                    name="cloudcephosd2003-dev",
                                    type="host",
                                )
                            ],
                            node_id=-11,
                            name="F4",
                            type="rack",
                        ),
                        OSDTreeNode(
                            crush_weight=1.7459716796875,
                            children=[
                                OSDTreeNode(
                                    crush_weight=1.7459716796875,
                                    children=[
                                        OSDTreeOSDNode(
                                            node_id=3,
                                            type="osd",
                                            children=[],
                                            osd_id=3,
                                            name="osd.3",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=0.87298583984375,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=2,
                                            type="osd",
                                            children=[],
                                            osd_id=2,
                                            name="osd.2",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=0.87298583984375,
                                        ),
                                    ],
                                    node_id=-5,
                                    name="cloudcephosd2002-dev",
                                    type="host",
                                )
                            ],
                            node_id=-9,
                            name="E4",
                            type="rack",
                        ),
                        OSDTreeNode(
                            crush_weight=1.7459716796875,
                            children=[
                                OSDTreeNode(
                                    crush_weight=1.7459716796875,
                                    children=[
                                        OSDTreeOSDNode(
                                            node_id=1,
                                            type="osd",
                                            children=[],
                                            osd_id=1,
                                            name="osd.1",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=0.87298583984375,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=0,
                                            type="osd",
                                            children=[],
                                            osd_id=0,
                                            name="osd.0",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=0.87298583984375,
                                        ),
                                    ],
                                    node_id=-3,
                                    name="cloudcephosd2001-dev",
                                    type="host",
                                )
                            ],
                            node_id=-13,
                            name="C8D5",
                            type="rack",
                        ),
                    ],
                    node_id=-1,
                    name="default",
                    type="root",
                ),
                stray=[],
            ),
        },
    }
)
def test_get_osd_tree(expected_tree: OSDTree, osd_tree_command_output: str):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(responses=[osd_tree_command_output]),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    gotten_tree = my_controller.get_osd_tree()

    assert gotten_tree == expected_tree


@parametrize(
    {
        "Host is present in an OSD tree and has expected properties": {
            "osd_tree": OSDTree(
                root_node=OSDTreeNode(
                    crush_weight=1.0,
                    node_id=-1,
                    name="root",
                    type="root",
                    children=[
                        OSDTreeNode(
                            crush_weight=1.0,
                            node_id=-12,
                            name="F4",
                            type="rack",
                            children=[],
                        ),
                        OSDTreeNode(
                            crush_weight=1.0,
                            node_id=-11,
                            name="E4",
                            type="rack",
                            children=[
                                OSDTreeNode(
                                    crush_weight=1.0,
                                    node_id=-2,
                                    name="host01",
                                    type="host",
                                    children=[
                                        OSDTreeOSDNode(
                                            node_id=101,
                                            type="osd",
                                            children=[],
                                            osd_id=101,
                                            name="osd.101",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=102,
                                            type="osd",
                                            children=[],
                                            osd_id=102,
                                            name="osd.102",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=103,
                                            type="osd",
                                            children=[],
                                            osd_id=103,
                                            name="osd.103",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=104,
                                            type="osd",
                                            children=[],
                                            osd_id=104,
                                            name="osd.104",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=105,
                                            type="osd",
                                            children=[],
                                            osd_id=105,
                                            name="osd.105",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=106,
                                            type="osd",
                                            children=[],
                                            osd_id=106,
                                            name="osd.106",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=107,
                                            type="osd",
                                            children=[],
                                            osd_id=107,
                                            name="osd.107",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=108,
                                            type="osd",
                                            children=[],
                                            osd_id=108,
                                            name="osd.108",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                    ],
                                ),
                                OSDTreeNode(
                                    crush_weight=1.0,
                                    node_id=-3,
                                    name="host02",
                                    type="host",
                                    children=[],
                                ),
                            ],
                        ),
                    ],
                ),
                stray=[],
            ),
        }
    }
)
def test_is_osd_host_valid_success(osd_tree: OSDTree):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    assert my_controller.is_osd_host_valid(osd_tree=osd_tree, hostname="host01") is True


@parametrize(
    {
        "Host is not present in the OSD tree": {
            "osd_tree": OSDTree(
                root_node=OSDTreeNode(
                    crush_weight=1.0,
                    node_id=-1,
                    name="root",
                    type="root",
                    children=[
                        OSDTreeNode(
                            crush_weight=1.0,
                            node_id=-11,
                            name="E4",
                            type="rack",
                            children=[
                                OSDTreeNode(crush_weight=1.0, node_id=-3, name="host02", type="host", children=[]),
                            ],
                        ),
                    ],
                ),
                stray=[],
            ),
        },
        "Host is present in the OSD tree and has wrong number of OSDs": {
            "osd_tree": OSDTree(
                root_node=OSDTreeNode(
                    crush_weight=1.0,
                    node_id=-1,
                    name="root",
                    type="root",
                    children=[
                        OSDTreeNode(
                            crush_weight=1.0,
                            name="E4",
                            node_id=-11,
                            type="rack",
                            children=[
                                OSDTreeNode(
                                    crush_weight=1.0,
                                    node_id=-2,
                                    name="host01",
                                    type="host",
                                    children=[
                                        OSDTreeOSDNode(
                                            node_id=101,
                                            type="osd",
                                            children=[],
                                            osd_id=101,
                                            name="osd.101",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                        OSDTreeOSDNode(
                                            node_id=102,
                                            type="osd",
                                            children=[],
                                            osd_id=102,
                                            name="osd.102",
                                            device_class=OSDClass.SSD,
                                            status=OSDStatus.UP,
                                            crush_weight=1.5,
                                        ),
                                    ],
                                ),
                                OSDTreeNode(crush_weight=1.0, node_id=-3, name="host02", type="host", children=[]),
                            ],
                        ),
                    ],
                ),
                stray=[],
            ),
        },
    }
)
def test_is_osd_host_valid_failure(osd_tree: OSDTree):
    my_controller = CephClusterController(
        remote=CephTestUtils.get_fake_remote(),
        cluster_name=CephClusterName.EQIAD1,
        spicerack=mock.MagicMock(spec=Spicerack),
    )

    assert my_controller.is_osd_host_valid(osd_tree=osd_tree, hostname="host01") is False
