#!/usr/bin/env python3
"""Ceph related library functions and classes."""
# pylint: disable=too-many-lines
from __future__ import annotations

import json
import logging
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import chain, cycle, islice
from typing import Any, Generator, Iterable, Literal, TypeVar, cast

from spicerack import Spicerack
from spicerack.remote import Remote, RemoteExecutionError
from wmflib.interactive import ask_confirmation

from wmcs_libs.alerts import SilenceID, remove_silence, silence_alert
from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CUMIN_UNSAFE_WITH_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    ArgparsableEnum,
    CommandRunnerMixin,
    UtilsForTesting,
    run_one_formatted,
    run_one_raw,
)
from wmcs_libs.inventory.ceph import CephClusterName, CephNodeRoleName
from wmcs_libs.inventory.libs import (
    generic_get_node_cluster_name,
    get_node_inventory_info,
    get_nodes_by_role,
    get_osd_drives_count,
)

LOGGER = logging.getLogger(__name__)
# list of alerts that are triggered by the cluster aside from the specifics for each node
OSD_EXPECTED_OS_DRIVES = 2

OSDTreeNodeType = Literal["host", "rack", "root", "osd"]


@dataclass(frozen=True)
class OSDIdNode:
    osd_id: int
    node_fqdn: str


T = TypeVar("T")


def round_robin(*iterables: Iterable[T]) -> Generator[T, None, None]:
    """
    roundrobin('ABC', 'D', 'EF') --> A D E B F C

    From https://docs.python.org/3.3/library/itertools.html#itertools-recipes
    """
    # Recipe credited to George Sakkis
    pending = len(iterables)
    next_fns = cycle(iter(it).__next__ for it in iterables)
    while pending:
        try:
            for next_fn in next_fns:
                yield next_fn()
        except StopIteration:
            pending -= 1
            next_fns = cycle(islice(next_fns, pending))


class CephException(Exception):
    """Parent exception for all ceph related issues."""


class CephClusterUnhealthy(CephException):
    """Risen when trying to act on an unhealthy cluster."""


class CephTimeout(CephException):
    """Risen when trying to act on an unhealthy cluster."""


class CephFlagSetError(CephException):
    """Risen when something failed when setting a flag in the cluster."""


class CephNoControllerNode(CephException):
    """Risen when there was no other controlling node found."""


class CephMalformedInfo(CephException):
    """Risen when the output of a command is not what was expected."""


class CephOSDFlag(ArgparsableEnum):
    """Possible OSD flags."""

    # cluster marked as full and stops serving writes
    FULL = "full"
    # stop serving writes and reads
    PAUSE = "pause"
    # avoid marking osds as up (serving traffic)
    NOUP = "noup"
    # avoid marking osds as down (stop serving traffic)
    NODOWN = "nodown"
    # avoid marking osds as out (get out of the cluster, would trigger
    # rebalancing)
    NOOUT = "noout"
    # avoid marking osds as in (get in the cluster, would trigger rebalancing)
    NOIN = "noin"
    # avoid backfills (asynchronous recovery from journal log)
    NOBACKFILL = "nobackfill"
    # avoid rebalancing (data rebalancing will stop)
    NOREBALANCE = "norebalance"
    # avoid recovery (synchronous recovery of raw data)
    NORECOVER = "norecover"
    # avoid running any scrub job (independent from deep scrubs)
    NOSCRUB = "noscrub"
    # avoid running any deep scrub job
    NODEEP_SCRUB = "nodeep-scrub"
    # avoid cache tiering activity
    NOTIERAGENT = "notieragent"
    # avoid snapshot trimming (async deletion of objects from deleted
    # snapshots)
    NOSNAPTRIM = "nosnaptrim"
    # explicit hard limit the pg log (don't use, deprecated feature)
    PGLOG_HARDLIMIT = "pglog_hardlimit"


class OSDInOut(ArgparsableEnum):
    IN = "in"
    OUT = "out"
    ALL = "all"


class OSDClass(ArgparsableEnum):
    """Supported OSD classes."""

    HDD = "hdd"
    SSD = "ssd"
    UNKNOWN = "unknown"

    @classmethod
    def from_str(cls, status_str: str) -> "OSDClass":
        """Get the osd class object from a string like the one from `ceph osd tree -f json`."""
        try:
            return cls(status_str)
        except ValueError:
            return cls.UNKNOWN


class OSDStatus(ArgparsableEnum):
    """Known ceph osd statuses."""

    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"

    @classmethod
    def from_str(cls, status_str: str) -> "OSDStatus":
        """Get the status object from a string like the one from `ceph osd tree -f json`."""
        try:
            return cls(status_str)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True)
class OSDTreeNode:
    """Generic osd tree node.

    Example of an entry:
    {
      "id": -65,
      "name": "cloudcephosd1033",
      "type": "host",
      "type_id": 1,
      "crush_weight": 0.87779,
      "reweight": 1.0,
      "pool_weights": {},
      "children": [
        262,
        261,
        260,
        259,
        258,
        257,
        256,
        255
      ]
    }
    """

    node_id: int
    name: str
    crush_weight: float
    reweight: float
    type: OSDTreeNodeType
    children: list[OSDTreeNode]


@dataclass(frozen=True)
class OSDTreeOSDNode(OSDTreeNode):
    """Class to bundle OSD data together.

    Example of source data:
    {
      "id": 238,
      "device_class": "ssd",
      "name": "osd.238",
      "type": "osd",
      "type_id": 0,
      "crush_weight": 1.7469940185546875,
      "depth": 3,
      "pool_weights": {},
      "exists": 1,
      "status": "up",
      "reweight": 1,
      "primary_affinity": 1
    }
    """

    osd_id: int
    device_class: OSDClass
    status: OSDStatus
    crush_weight: float
    reweight: float

    @classmethod
    def from_json_data(cls, json_data: dict[str, Any]) -> "OSDTreeOSDNode":
        """Get an osd class from the osd entry in the output of `ceph osd tree -f json`."""
        return cls(
            node_id=json_data["id"],
            type=json_data["type"],
            osd_id=json_data["id"],
            name=json_data["name"],
            device_class=OSDClass.from_str(json_data["device_class"]),
            status=OSDStatus.from_str(json_data["status"]),
            crush_weight=json_data["crush_weight"],
            reweight=json_data["reweight"],
            children=[],
        )


@dataclass(frozen=True)
class OSDTree:
    """Simple osd tree representation."""

    root_node: OSDTreeNode
    stray: list[dict[str, Any]]

    @staticmethod
    def _get_nodes_by_type(node: OSDTreeNode, wanted_type: OSDTreeNodeType) -> Iterable[OSDTreeNode]:
        """Helper method to retrieve the osd nodes."""
        extra_nodes: list[OSDTreeNode] = []
        if node.type == wanted_type:
            extra_nodes = [node]

        return chain(
            extra_nodes, *[OSDTree._get_nodes_by_type(node=child, wanted_type=wanted_type) for child in node.children]
        )

    def get_nodes_by_type(self, wanted_type: OSDTreeNodeType) -> Iterable[OSDTreeNode]:
        """Get all the nodes matching a type no matter where in the tree."""
        return self._get_nodes_by_type(node=self.root_node, wanted_type=wanted_type)


@dataclass(frozen=True)
class MGRMap:
    """Ceph mgrmap structure in the status."""

    available: bool
    num_standbys: int
    modules: list[str]
    services: dict[str, str]

    @classmethod
    def from_dict(cls, obj_dict: dict[str, Any]) -> "MGRMap":
        """Create the MGRMap from the output of ceph status -f json | jq '.mgrmap'"""
        return cls(
            available=obj_dict["available"],
            num_standbys=obj_dict.get("num_standbys", 0),
            modules=obj_dict.get("modules", []),
            services=obj_dict.get("services", {}),
        )


@dataclass(frozen=True)
class CephClusterStatus:
    """Status of a CEPH cluster."""

    status_dict: dict[str, Any]

    def get_osdmap_set_flags(self) -> set[CephOSDFlag]:
        """Get osdmap set flags."""
        osd_maps = self.status_dict["health"]["checks"].get("OSDMAP_FLAGS")
        if not osd_maps:
            return set()

        raw_flags_line = osd_maps["summary"]["message"]
        if "flag(s) set" not in raw_flags_line:
            return set()

        # ex: "noout,norebalance flag(s) set"
        flags = raw_flags_line.split(" ")[0].split(",")
        return set(CephOSDFlag(flag) for flag in flags)

    @staticmethod
    def _filter_out_octopus_upgrade_warns(status: dict[str, Any]) -> dict[str, Any]:
        # ignore temporary alert for octopus upgrade
        # https://docs.ceph.com/en/latest/security/CVE-2021-20288/#recommendations
        new_status = deepcopy(status)
        there_were_health_checks = bool(len(new_status["health"]["checks"]) > 0)

        if "AUTH_INSECURE_GLOBAL_ID_RECLAIM" in new_status["health"]["checks"]:
            del new_status["health"]["checks"]["AUTH_INSECURE_GLOBAL_ID_RECLAIM"]

        if "AUTH_INSECURE_GLOBAL_ID_RECLAIM_ALLOWED" in new_status["health"]["checks"]:
            del new_status["health"]["checks"]["AUTH_INSECURE_GLOBAL_ID_RECLAIM_ALLOWED"]

        # if there were no health checks to start with, something was very wrong in the cluster.
        if there_were_health_checks and len(new_status["health"]["checks"]) == 0:
            new_status["health"]["status"] = "HEALTH_OK"

        return new_status

    def is_cluster_in_maintenance(self) -> bool:
        """Return if the cluster is in HEALTH_WARN only because it's in maintenance status."""
        # ignore temporary alert for octopus upgrade
        # https://docs.ceph.com/en/latest/security/CVE-2021-20288/#recommendations
        temp_status = self._filter_out_octopus_upgrade_warns(self.status_dict)

        if temp_status["health"]["status"] == "HEALTH_OK":
            return False

        if "OSDMAP_FLAGS" in temp_status["health"]["checks"] and len(temp_status["health"]["checks"]) == 1:
            current_flags = self.get_osdmap_set_flags()
            return current_flags.issubset({CephOSDFlag.NOOUT, CephOSDFlag.NOREBALANCE, CephOSDFlag.NOIN})

        return False

    def check_healthy(
        self,
        consider_maintenance_healthy: bool = False,
        health_issues_to_ignore: Iterable[str] | None = None,
    ) -> None:
        """Check if the cluster is healthy."""
        # ignore temporary alert for octopus upgrade
        # https://docs.ceph.com/en/latest/security/CVE-2021-20288/#recommendations
        temp_status = self._filter_out_octopus_upgrade_warns(self.status_dict)

        if temp_status["health"]["status"] == "HEALTH_OK":
            return

        for health_issue in health_issues_to_ignore or []:
            if health_issue in temp_status["health"]["checks"]:
                del temp_status["health"]["checks"][health_issue]

            if not temp_status["health"]["checks"]:
                return

        if (
            consider_maintenance_healthy
            and self.is_cluster_in_maintenance()
            and len(temp_status["health"]["checks"]) == 1
        ):
            return

        if temp_status["health"]["status"] != "HEALTH_OK":
            raise CephClusterUnhealthy(
                f"The cluster is currently in an unhealthy status: \n{json.dumps(self.status_dict['health'], indent=4)}"
            )

    def get_in_progress(self) -> dict[str, Any]:
        """Get the current in-progress events."""
        return self.status_dict.get("progress_events", {})

    def get_health_issues(self) -> dict[str, Any]:
        """Get the current health issues."""
        return self.status_dict.get("health", {}).get("checks", {})

    def get_mgrmap(self) -> MGRMap:
        """Get mgrmap from status"""
        return MGRMap.from_dict(self.status_dict["mgrmap"])


class CephOSDNodeController:
    """Controller for a CEPH OSD node."""

    def __init__(self, remote: Remote, node_fqdn: str):
        """Init."""
        self._remote = remote
        self.node_fqdn = node_fqdn
        self._node = self._remote.query(f"D{{{self.node_fqdn}}}", use_sudo=True)

    @classmethod
    def _is_device_available(cls, device_info: dict[str, Any]) -> bool:
        def _is_disk() -> bool:
            return device_info.get("type") == "disk"

        def _does_not_have_partitions() -> bool:
            return not device_info.get("children")

        def _its_not_mounted() -> bool:
            return not device_info.get("mountpoint")

        return _is_disk() and _does_not_have_partitions() and _its_not_mounted()

    def _dir_exists(self, dirpath):
        try:
            run_one_raw(
                command=["test", "-d", dirpath],
                node=self._node,
                cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
            )
        except RemoteExecutionError:
            return False

        return True

    def _is_device_ready_for_activation(self, device_info: dict[str, Any], osd_id: str) -> bool:
        def _is_disk() -> bool:
            return device_info.get("type") == "disk"

        def _has_one_partition() -> bool:
            return len(device_info.get("children", [])) == 1

        def _its_not_mounted() -> bool:
            return not device_info.get("mountpoint")

        def _no_osd_dir() -> bool:
            osd_dir = f"/var/lib/ceph/osd/ceph-{osd_id}"
            return not self._dir_exists(osd_dir)

        return _is_disk() and _has_one_partition() and _its_not_mounted() and _no_osd_dir()

    def do_lsblk(self, device: str | None = None) -> list[dict[str, Any]]:
        """Simple lsblk on the host to get the devices."""
        command = ["lsblk", "--json", "--bytes"]
        if device:
            command.append(device)

        structured_output = run_one_formatted(
            command=command,
            node=self._node,
            cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
        )
        if not isinstance(structured_output, dict):
            raise TypeError(f"Was expecting a dict, got {structured_output}")

        if "blockdevices" not in structured_output:
            raise CephMalformedInfo(
                f"Missing 'blockdevices' on lsblk output: {json.dumps(structured_output, indent=4)}"
            )

        return structured_output["blockdevices"]

    def get_osd_partitions(self) -> dict:
        """Get data about existing ceph partitions"""
        lvm_info = run_one_formatted(command=["ceph-volume", "lvm", "list", "--format", "json"], node=self._node)
        if not isinstance(lvm_info, dict):
            raise TypeError(f"Was expecting a dict, got {lvm_info}")
        return lvm_info

    def get_available_devices(self) -> list[str]:
        """Get the current available devices in the node."""
        return [
            f"/dev/{device_info['name']}"
            for device_info in self.do_lsblk()
            if self._is_device_available(device_info=device_info)
        ]

    def get_inactive_devices(self) -> list[str]:
        """Get the current available devices in the node."""
        ceph_lvm_info = self.get_osd_partitions()
        lvm_dict = {lvm[0]["devices"][0]: lvm[0]["tags"]["ceph.osd_id"] for lvm in list(ceph_lvm_info.values())}

        return [
            f"/dev/{device_info['name']}"
            for device_info in self.do_lsblk()
            if f'/dev/{device_info["name"]}' in lvm_dict
            and self._is_device_ready_for_activation(
                device_info=device_info, osd_id=lvm_dict[f'/dev/{device_info["name"]}']
            )
        ]

    def zap_device(self, device_path: str) -> None:
        """Zap the given device.

        NOTE: this destroys all the information in the device!
        """
        run_one_raw(command=["ceph-volume", "lvm", "zap", "--destroy", device_path], node=self._node)

    def initialize_and_start_osd(self, device_path: str) -> None:
        """Setup and start a new osd on the given device."""
        run_one_raw(command=["ceph-volume", "lvm", "create", "--bluestore", "--data", device_path], node=self._node)

    def activate_osd(self, osd_id, fsid) -> None:
        """Start an existing osd on the given device."""
        run_one_raw(command=["ceph-volume", "lvm", "activate", osd_id, fsid], node=self._node)

    def add_all_available_devices(self, interactive: bool = True) -> None:
        """Discover and add all the available devices of the node as new OSDs."""
        available_devices = self.get_available_devices()
        if interactive and available_devices:
            ask_confirmation(
                f"I'm going to destroy and create a new OSD on {self.node_fqdn}:{', '.join(available_devices)}."
            )

        for device_path in available_devices:
            self.zap_device(device_path=device_path)
            self.initialize_and_start_osd(device_path=device_path)

    def activate_inactive_devices(self, interactive: bool = True) -> None:
        """Re-enable osd devices that ceph mons know about but which are inactive on the osd node"""

        inactive_devices = self.get_inactive_devices()
        if interactive and inactive_devices:
            ask_confirmation(f"I'm going to activate OSDs {', '.join(inactive_devices)} on {self.node_fqdn}.")

        ceph_lvm_info = self.get_osd_partitions()

        for osd_id, partitions in ceph_lvm_info.items():
            if len(partitions) != 1:
                LOGGER.warning(
                    "Ceph osd %s has %d  partitions associated. We don't know what to do with that.",
                    osd_id,
                    len(partitions),
                )
                continue
            partition = partitions[0]
            if partition["devices"][0] in inactive_devices:
                if len(list(partition["devices"])) != 1:
                    LOGGER.warning(
                        "Ceph osd partition %s has %d volumes. We don't know what to do with that.",
                        osd_id,
                        len(partition["devices"]),
                    )
                    continue

                fsid = partition["tags"]["ceph.osd_fsid"]
                LOGGER.info("activating osd %s on device %s, fsid %s", osd_id, partition["devices"][0], fsid)
                self.activate_osd(osd_id, partition["tags"]["ceph.osd_fsid"])

    def check_jumbo_frames(self) -> bool:
        """Check if this node network is ready to be setup as a new osd.

        We rely on the prometheus-node-pinger script to be installed in the node (added by puppet).
        """
        try:
            run_one_raw(
                command=["prometheus-node-pinger"],
                node=self._node,
                cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
            )
        except RemoteExecutionError as err:
            LOGGER.warning("Failed network checks %s", str(err))
            return False

        return True

    def stop_osd(self, osd_id: int) -> str:
        """Stops an osd daemon."""
        return run_one_raw(
            ["systemctl", "stop", f"ceph-osd@{osd_id}"],
            node=self._node,
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )

    def stop_osds(self, osd_ids: list[int]) -> None:
        """Stops all the given OSD daemons from the OSD host."""
        for osd_id in osd_ids:
            self.stop_osd(osd_id=osd_id)


class CephClusterController(CommandRunnerMixin):
    """Controller for a CEPH cluster."""

    CLUSTER_ALERT_MATCH: dict[str, str | int | float | bool] = {
        "name": "service",
        "value": "~.*ceph.*",
        "isRegex": True,
    }

    def __init__(
        self,
        remote: Remote,
        cluster_name: CephClusterName,
        spicerack: Spicerack,
        os_hw_raid: bool = False,
        expected_drives: int = 0,
    ):
        """Init."""
        self._remote = remote
        self.cluster_name = cluster_name
        self.controlling_node_fqdn = get_mon_nodes(cluster_name)[0]
        self._controlling_node = self._remote.query(f"D{{{self.controlling_node_fqdn}}}", use_sudo=True)
        if expected_drives:
            self.expected_osd_drives_per_host = expected_drives
        else:
            self.expected_osd_drives_per_host = get_osd_drives_count(cluster_name)
        self.os_hw_raid = os_hw_raid
        self._spicerack = spicerack
        super().__init__(command_runner_node=self._controlling_node)

    def _get_full_command(
        self, *command: str, json_output: bool = True, project_as_arg: bool = False, with_env_var: bool = True
    ):
        if json_output:
            format_args = ["-f", "json"]
        else:
            format_args = []

        return ["ceph", *command, *format_args]

    def get_nodes(self) -> dict[str, Any]:
        """Get the nodes currently in the cluster."""
        # There's usually a couple empty lines before the json data
        return self.run_formatted_as_dict("node", "ls", last_line_only=True)

    def get_nodes_domain(self) -> str:
        """Get the network domain for the nodes in the cluster."""
        info = get_node_inventory_info(node=self.controlling_node_fqdn)
        return f"{info.site_name.value}.wmnet"

    def change_controlling_node(self) -> None:
        """Change the current node being used to interact with the cluster for another one."""
        current_monitor_name = self.controlling_node_fqdn.split(".", 1)[0]
        nodes = self.get_nodes()
        try:
            another_monitor = next(node_host for node_host in nodes["mon"].keys() if node_host != current_monitor_name)
        except StopIteration as error:
            raise CephNoControllerNode(
                f"Unable to find any other mon node to control the cluster, got nodes: {nodes}"
            ) from error

        self.controlling_node_fqdn = f"{another_monitor}.{self.get_nodes_domain()}"
        self._controlling_node = self._remote.query(f"D{{{self.controlling_node_fqdn}}}", use_sudo=True)
        LOGGER.info("Changed to node %s to control the CEPH cluster.", self.controlling_node_fqdn)

    def get_cluster_status(self) -> CephClusterStatus:
        """Get the current cluster status."""
        try:
            cluster_status_output = self.run_formatted_as_dict("status", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)
        except Exception as error:  # noqa: broad-except
            LOGGER.info("Retrying get_cluster_status (got error %s)", str(error))
            cluster_status_output = self.run_formatted_as_dict("status", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)

        return CephClusterStatus(status_dict=cluster_status_output)

    def is_osdmap_flag_set(self, flag: CephOSDFlag) -> bool:
        """Check if a given flag is set."""
        return flag in self.get_cluster_status().get_osdmap_set_flags()

    def set_osdmap_flag(self, flag: CephOSDFlag) -> None:
        """Set one of the osdmap flags."""
        set_osdmap_flag_result = self.run_raw(
            "osd", "set", flag.value, json_output=False, cumin_params=CUMIN_UNSAFE_WITH_OUTPUT
        )
        if not re.match(f"(^|\n){flag.value} is set", set_osdmap_flag_result):
            raise CephFlagSetError(f"Unable to set `{flag.value}` on the cluster, got output: {set_osdmap_flag_result}")

    def unset_osdmap_flag(self, flag: CephOSDFlag) -> None:
        """Unset one of the osdmap flags."""
        unset_osdmap_flag_result = self.run_raw(
            "osd", "unset", flag.value, json_output=False, cumin_params=CUMIN_UNSAFE_WITH_OUTPUT
        )
        if not re.match(f"(^|\n){flag.value} is unset", unset_osdmap_flag_result, re.MULTILINE):
            raise CephFlagSetError(
                f"Unable to unset `{flag.value}` on the cluster, got output: {unset_osdmap_flag_result}"
            )

    def set_osd_class(self, osd_id: int, osd_class: OSDClass) -> None:
        """Change an osd class (ex. from hdd to ssd).

        Note that `osd_id` is the number of the osd, for example, for osd.195, that would be the integer 195.
        """
        self.run_raw(
            "osd", "crush", "rm-device-class", f"{osd_id}", json_output=False, cumin_params=CUMIN_UNSAFE_WITH_OUTPUT
        )
        self.run_raw(
            "osd",
            "crush",
            "set-device-class",
            osd_class.value,
            f"{osd_id}",
            json_output=False,
            cumin_params=CUMIN_UNSAFE_WITH_OUTPUT,
        )

    def downtime_cluster_alerts(
        self, reason: str, duration: timedelta = timedelta(hours=4), task_id: str | None = None
    ) -> list[SilenceID]:
        """Downtime all the known cluster-wide alerts (the ones not related to a specific ceph node)."""
        silences = []
        # There's only one alert left
        silences.append(
            silence_alert(
                spicerack=self._spicerack,
                duration=duration,
                task_id=task_id,
                comment=f"Downtiming alert from cookbook - {reason}",
                extra_matchers=[self.CLUSTER_ALERT_MATCH],
            )
        )

        return silences

    def uptime_cluster_alerts(self, silences: list[SilenceID]) -> None:
        """Enable again all the alert for the cluster."""
        for silence in silences:
            remove_silence(spicerack=self._spicerack, silence_id=silence)

    def set_maintenance(self, reason: str, force: bool = False, task_id: str | None = None) -> list[SilenceID]:
        """Set maintenance and mute any cluster-wide alerts.

        Returns the list of alert silences, to pass back to unset_maintenance for example.
        """
        silences = self.downtime_cluster_alerts(task_id=task_id, reason=reason)
        cluster_status = self.get_cluster_status()
        if cluster_status.is_cluster_in_maintenance():
            LOGGER.info("Cluster already in maintenance status.")
            return silences

        try:
            cluster_status.check_healthy()

        except CephClusterUnhealthy:
            if not force:
                LOGGER.warning(
                    "Cluster is not in a healthy status, putting it in maintenance might stop any recovery processes. "
                    "Use --force to ignore this message and set the cluster in maintenance mode anyhow."
                )
                raise

            LOGGER.info(
                (
                    "Cluster is not in a healthy status, putting it in maintenance might stop any recovery processes. "
                    "Continuing as --force was specified. Current status:\n%s"
                ),
                json.dumps(cluster_status.status_dict["health"], indent=4),
            )

        self.set_osdmap_flag(flag=CephOSDFlag("noout"))
        self.set_osdmap_flag(flag=CephOSDFlag("norebalance"))
        return silences

    def unset_maintenance(self, silences: list[SilenceID], force: bool = False) -> None:
        """Unset maintenance and remove any cluster-wide alert silences."""
        cluster_status = self.get_cluster_status()
        try:
            cluster_status.check_healthy(consider_maintenance_healthy=True)

        except CephClusterUnhealthy:
            if not force:
                LOGGER.warning(
                    "Cluster is not in a healthy status, getting it out of maintenance might have undesirable "
                    "effects. Use --force to ignore this message and unset the cluster maintenance mode anyhow."
                )
                raise

            LOGGER.info(
                (
                    "Cluster is not in a healthy status, getting it out of maintenance might have undesirable "
                    "state. Continuing as --force was specified. Current status: \n%s"
                ),
                json.dumps(cluster_status.status_dict["health"], indent=4),
            )

        self.unset_osdmap_flag(flag=CephOSDFlag("noout"))
        self.unset_osdmap_flag(flag=CephOSDFlag("norebalance"))
        self.uptime_cluster_alerts(silences=silences)

    def wait_for_rebalance(self, timeout: timedelta = timedelta(seconds=600)) -> bool:
        """Wait until a cluster in rebalance has finished.

        Returns True if it had to wait at any time, False if there was no misplaced objects to rebalance.
        """
        check_interval = timedelta(seconds=10)
        start_time = datetime.now()
        cur_time = start_time
        cluster_status = self.get_cluster_status()
        had_to_wait = False
        # the first rounds this might increase, but it's expected to stop increasing once the cluster started
        # rebalancing
        max_number_of_misplaced = 0
        while cur_time - start_time < timeout:
            misplaced_objects = cluster_status.status_dict.get("pgmap", {}).get("misplaced_objects", 0)
            max_number_of_misplaced = (
                misplaced_objects if misplaced_objects > max_number_of_misplaced else max_number_of_misplaced
            )
            if not misplaced_objects:
                LOGGER.info(
                    "No misplaced objects found, returning, took %s to stabilize", (datetime.now() - start_time)
                )
                return had_to_wait

            LOGGER.debug("Misplaced objects found, waiting")
            had_to_wait = True
            objects_placed = max_number_of_misplaced - misplaced_objects
            if cur_time != start_time:
                recovery_speed = objects_placed / (cur_time - start_time).total_seconds()
            else:
                recovery_speed = 0

            if recovery_speed:
                estimated_elapsed_time = misplaced_objects / recovery_speed
            else:
                estimated_elapsed_time = -1
            LOGGER.info(
                (
                    "Cluster still has (%d) misplaced objects, at the current %d obj/s should take %s to "
                    "finish, waiting %s (timeout=%s, elapsed=%s)..."
                ),
                misplaced_objects,
                recovery_speed,
                timedelta(seconds=estimated_elapsed_time),
                check_interval,
                timeout,
                cur_time - start_time,
            )

            time.sleep(check_interval.total_seconds())
            cur_time = datetime.now()
            cluster_status = self.get_cluster_status()

        raise CephTimeout(
            f"Waited {timeout} for the cluster to finish rebalancing, but it never did, current state:\n"
            f"\n{json.dumps(cluster_status.status_dict, indent=4)}"
        )

    def wait_for_in_progress_events(self, timeout: timedelta = timedelta(minutes=10)) -> bool:
        """Wait until a cluster in progress events have finished.

        Note that this is different than rebalancing or healing, but somewhat a mixture :/
        If you want to check rebalancing, use the specific one for it.

        Returns True if it had to wait at any time, False if there were no in-progress tasks.
        """
        check_interval = timedelta(seconds=10)
        start_time = datetime.now()
        cur_time = start_time
        cluster_status = self.get_cluster_status()
        had_to_wait = False
        while cur_time - start_time < timeout:
            in_progress_events = cluster_status.get_in_progress()
            if not in_progress_events:
                LOGGER.info("No in-progress events found, returning")
                return had_to_wait

            LOGGER.info("In-progress events found, waiting")
            had_to_wait = True
            mean_progress = (
                sum(event["progress"] for event in in_progress_events.values()) * 100 / len(in_progress_events)
            )
            LOGGER.info(
                "Cluster still has (%d) in-progress events, %.2f%% done, waiting %s (timeout=%s)...",
                len(in_progress_events),
                mean_progress,
                check_interval,
                timeout,
            )

            time.sleep(check_interval.total_seconds())
            cur_time = datetime.now()
            cluster_status = self.get_cluster_status()

        raise CephTimeout(
            f"Waited {timeout} for the cluster to finish in-progress events, but it never did, current state:\n"
            f"\n{json.dumps(cluster_status.get_in_progress(), indent=4)}"
        )

    def wait_for_one_manager_standby(
        self,
        timeout: timedelta = timedelta(minutes=10),
    ) -> None:
        """Wait until there's at least one mgr in standby."""
        check_interval = timedelta(seconds=10)
        start_time = datetime.now()
        cur_time = start_time
        while cur_time - start_time < timeout:
            if self.get_cluster_status().get_mgrmap().num_standbys:
                return

            time.sleep(check_interval.total_seconds())
            cur_time = datetime.now()

        cluster_status = self.get_cluster_status()
        raise CephClusterUnhealthy(
            f"Waited {timeout} for any manager to become standby, but it never did, current state:\n"
            f"\n{json.dumps(cluster_status.status_dict['health'], indent=4)}"
        )

    def wait_for_cluster_healthy(
        self,
        consider_maintenance_healthy: bool = False,
        # Ceph uses the 15-minute average to measure health, so we need to wait
        #  a long time for it to feel better after a reboot
        timeout: timedelta = timedelta(minutes=30),
        health_issues_to_ignore: Iterable[str] | None = None,
    ) -> None:
        """Wait until a cluster becomes healthy."""
        check_interval = timedelta(seconds=10)
        start_time = datetime.now()
        cur_time = start_time
        while cur_time - start_time < timeout:
            try:
                self.get_cluster_status().check_healthy(
                    consider_maintenance_healthy=consider_maintenance_healthy,
                    health_issues_to_ignore=health_issues_to_ignore or [],
                )
                return

            except CephClusterUnhealthy:
                LOGGER.info(
                    "%s have passed, but the cluster is still not healthy, waiting %s (timeout=%s)...",
                    cur_time - start_time,
                    check_interval,
                    timeout,
                )

            time.sleep(check_interval.total_seconds())
            cur_time = datetime.now()

        cluster_status = self.get_cluster_status()
        raise CephClusterUnhealthy(
            f"Waited {timeout} for the cluster to become healthy, but it never did, current state:\n"
            f"\n{json.dumps(cluster_status.status_dict['health'], indent=4)}"
        )

    def get_osd_tree(self) -> OSDTree:
        """Retrieve the osd tree, already parsed into a tree structure."""

        def _get_expanded_node(plain_node: dict[str, Any], all_nodes: dict[int, dict[str, Any]]) -> OSDTreeNode:
            # We expect the "osd" nodes to always be leaf nodes of the tree
            if plain_node.get("type") == "osd":
                return OSDTreeOSDNode.from_json_data(plain_node)

            # We expect other node types to always have a "children" attribute (can be an empty list)
            if plain_node.get("children", None) is None:
                raise CephException(f"Unexpected leaf node that is not an OSD: {plain_node}")

            children_ids = plain_node["children"]
            children = [_get_expanded_node(all_nodes[child_id], all_nodes) for child_id in children_ids]
            return OSDTreeNode(
                children=children,
                node_id=plain_node["id"],
                type=plain_node["type"],
                name=plain_node["name"],
                crush_weight=plain_node.get("crush_weight", sum(child.crush_weight for child in children)),
                reweight=plain_node.get("reweight", 1.0),
            )

        def _get_expanded_root_node(nodes_list: list[dict[str, Any]]) -> OSDTreeNode:
            id_to_nodes: dict[int, dict[str, Any]] = {node["id"]: node for node in nodes_list}
            root_node = next(node for node in nodes_list if node["type"] == "root")
            return _get_expanded_node(plain_node=root_node, all_nodes=id_to_nodes)

        flat_nodes = self.run_formatted_as_dict("osd", "tree", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)
        return OSDTree(
            root_node=_get_expanded_root_node(nodes_list=flat_nodes["nodes"]),
            # TODO: update the following to a useful structure if it's ever needed
            stray=flat_nodes["stray"],
        )

    def get_osd_size_bytes(self, osd_id: int, osd_fqdn: str) -> int:
        osd_host = osd_fqdn.split(".", 1)[0]
        osd_device = self.get_device_for_osds(hostname=osd_host, osds=[osd_id])[0]
        osd_controller = CephOSDNodeController(remote=self._remote, node_fqdn=osd_fqdn)
        lsblk = osd_controller.do_lsblk(device=osd_device)
        return lsblk[0]["size"]

    def get_all_osd_ips(self) -> set[str]:
        """Returns all the known ips for all the osd, deduplicated.

        This includes the public and cluster ips, useful to run tests.
        """
        osd_dump = self.run_formatted_as_dict("osd", "dump", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)
        all_osd_ips: set[str] = set()
        for osd in osd_dump.get("osds", []):
            public_addr = osd["public_addr"].split(":", 1)[0]
            all_osd_ips.add(public_addr)
            cluster_addr = osd["cluster_addr"].split(":", 1)[0]
            all_osd_ips.add(cluster_addr)

        return all_osd_ips

    def get_osd_for_devices(self, hostname: str, devices: list[str]) -> list[int]:
        """Given a host and a list of device names (ex. sda) returns the osd that uses it."""

        host_devices = self.run_formatted_as_list("device", "ls-by-host", hostname)
        # the devices are only passed as names unfortunately, the paths are the full disk path (not so useful), see
        # the example below
        device_names = [device.rsplit("/", 1)[-1] for device in devices]
        # Example of return value:
        # [
        #   {
        #     "devid": "MTFDDAK1T9TDN_194725128AB3",
        #     "location": [
        #       {
        #         "host": "cloudcephosd1009",
        #         "dev": "sdg",
        #         "path": "/dev/disk/by-path/pci-0000:18:00.0-scsi-0:0:6:0"
        #       }
        #     ],
        #     "daemons": [
        #       "osd.20"
        #     ]
        #   }
        # ]
        osds = [
            # we have only one daemon per-device
            int(host_device["daemons"][0].split(".", 1)[-1])
            for host_device in host_devices
            if host_device["location"][0]["dev"] in device_names
        ]
        return osds

    def get_device_for_osds(self, hostname: str, osds: list[int]) -> list[str]:
        """Given a host and a list of osd ids (ex. 247) returns the devices that correspond to those osds."""

        host_devices = self.run_formatted_as_list(
            "device", "ls-by-host", hostname, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT
        )
        # Example of return value:
        # [
        #   {
        #     "devid": "MTFDDAK1T9TDN_194725128AB3",
        #     "location": [
        #       {
        #         "host": "cloudcephosd1009",
        #         "dev": "sdg",
        #         "path": "/dev/disk/by-path/pci-0000:18:00.0-scsi-0:0:6:0"
        #       }
        #     ],
        #     "daemons": [
        #       "osd.20"
        #     ]
        #   }
        # ]
        devices = [
            f"/dev/{host_device['location'][0]['dev']}"
            for host_device in host_devices
            # we have only one daemon per-device
            if int(host_device["daemons"][0].split(".", 1)[-1]) in osds
        ]
        return devices

    def crush_reset_weight_osd(self, osd_id: int, node_fqdn: str) -> bool:
        """Re-weights an OSD daemon at the CRUSH table.

        Returns True if any changes were made, False otherwise.
        """
        osd_size_bytes = self.get_osd_size_bytes(osd_id=osd_id, osd_fqdn=node_fqdn)
        # TiB as a float (kb * mb * gb * tb)
        new_weight = osd_size_bytes / (1024 * 1024 * 1024 * 1024)

        if new_weight <= 0:
            raise CephException(
                "Unable to guess the proper crush weight for the osd, you might have to pass one, gotten from "
                f"the osd size from node:\n{node_fqdn}"
            )

        return self.crush_reweight_osd(osd_id=osd_id, new_weight=new_weight)

    def reweight_osd(self, osd_id: int, new_weight: float) -> None:
        """Re-weights an OSD daemon.

        Note that this is not changing the crush table, but the reweight value, see:
            https://ceph.io/en/news/blog/2014/difference-between-ceph-osd-reweight-and-ceph-osd-crush-reweight/
        """
        if new_weight > 1.0 or new_weight < 0.0:
            raise ValueError(f"Reweighting an osd needs a float between 0.0 and 1.0, got {new_weight}")

        self.run_raw(
            "osd",
            "reweight",
            f"osd.{osd_id}",
            str(new_weight),
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )
        LOGGER.info("[osd.%d] Reweighted to %f", osd_id, new_weight)

    def crush_reweight_osd(self, osd_id: int, new_weight: float = -1.0) -> bool:
        """Re-weights an OSD daemon at the CRUSH table.

        Note that this is actually changing crush table, not the temporary reweight, see:
            https://ceph.io/en/news/blog/2014/difference-between-ceph-osd-reweight-and-ceph-osd-crush-reweight/

        Returns True if any changes were made, False otherwise.
        """
        cur_weight = next(
            (
                osd.crush_weight
                for osd in self.get_osd_tree().get_nodes_by_type(wanted_type="osd")
                if osd.name == f"osd.{osd_id}"
            ),
            None,
        )

        if cur_weight == new_weight:
            LOGGER.info("[osd.%d] Skipping crush reweight, already at weight %f", osd_id, new_weight)
            return False

        response = self.run_raw(
            "osd",
            "crush",
            "reweight",
            f"osd.{osd_id}",
            str(new_weight),
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )
        if f"reweighted item id {osd_id}" in response:
            LOGGER.info("[osd.%d] Crush reweighted to %f", osd_id, new_weight)
            return True

        raise CephException(f"Unexpected response when reweighting osd {osd_id} to {new_weight}: {response}")

    def mark_osd_in(self, osd_id: int) -> bool:
        """Mark an osd as in.

        This will make the mons start assigning PGs to it and (if it's weight is >0) start rebalancing.

        Returns True if the osd was out, False if it was already in.
        """
        response = self.run_raw("osd", "in", f"osd.{osd_id}", cumin_params=CUMIN_UNSAFE_WITH_OUTPUT)
        if "marked in" in response:
            return True

        if "already in" in response:
            return False

        raise CephException(f"Unexpected response when marking osd {osd_id} in: {response}")

    def mark_osd_out(self, osd_id: int) -> bool:
        """Mark an osd as out of the cluster.

        This will make the mons stop assigning PGs to it and (if it's weight was >0) start rebalancing.

        Returns True if the osd was in, False if it was already out.
        """
        response = self.run_raw("osd", "out", f"osd.{osd_id}", cumin_params=CUMIN_UNSAFE_WITH_OUTPUT)
        if "marked out" in response:
            return True

        if "already out" in response:
            return False

        raise CephException(f"Unexpected response when marking osd {osd_id} out: {response}")

    def drain_osds_in_chunks(
        self, osd_ids: list[int], batch_size: int = 0, be_unsafe: bool = False, wait: bool = True
    ) -> bool:
        """Drains the given osds in chunks.

        Return True if there were any osds removed (so you can decide if waiting for rebalancing or not).
        """
        start_time = datetime.now()
        timeout = timedelta(hours=5)

        if batch_size == 0:
            batch_size = len(osd_ids)

        chunk_start = 0

        def info(msg, *args):
            LOGGER.info(f"[%d/%d osds] {msg}", chunk_start, len(osd_ids), *args)

        any_changes = False
        info("Draining osds: %s", str(osd_ids))
        for chunk_start in range(0, len(osd_ids), batch_size):
            chunk_num = chunk_start // batch_size
            next_chunk = osd_ids[chunk_start : chunk_start + batch_size]
            info(
                "Draining osd batch %d: %s",
                chunk_num + 1,
                str(next_chunk),
            )
            had_changes = self.drain_osds(osd_ids=next_chunk, be_unsafe=be_unsafe)
            if wait and had_changes:
                info("Waiting for the cluster to shift data around...")
                # give some time for the cluster to start shifting things around
                while not self.wait_for_rebalance(timeout=timeout):
                    info("Rebalancing has not started yet, sleeping another 10s for the rebalance to start")
                    time.sleep(10)
            elif not had_changes:
                info("No changes to the cluster made, draining the next batch...")
            elif had_changes:
                any_changes = True

        chunk_start = len(osd_ids)
        end_time = datetime.now()
        info("All osds drained (%s), took %s", osd_ids, (end_time - start_time))
        return any_changes

    def drain_osds(self, osd_ids: list[int], be_unsafe: bool = False) -> bool:
        """Drains many OSD daemons by setting their weight to 0 and forcing ceph to rebalance it's data somewhere else.

        NOTE: prefer using `drain_osds_in_chunks` to better control the load of the cluster and recovery rate.

        This is different from depooling them one by one as in it will check if the cluster is consistent when
        depooling them together, instead of one after the other.

        Returns True if any osds were actually drained, False otherwise.
        """
        if not be_unsafe:
            # last check just to make sure
            failures = self.check_osds_ok_to_stop(osd_ids=osd_ids)
            if failures:
                raise CephException(
                    f"Depooling the osds {osd_ids} will put the cluster in an unstable state, if you are sure call "
                    "this function again with `be_unsafe=True`: "
                    "\n".join(failures)
                )

        any_changes = False
        for osd_id in osd_ids:
            new_changes = self.crush_reweight_osd(osd_id=osd_id, new_weight=0.0)
            # python short-circuits the binary expressions, so keeping the action separated to execute it no matter what
            any_changes = any_changes or new_changes

        for osd_id in osd_ids:
            self.mark_osd_out(osd_id=osd_id)

        return any_changes

    def undrain_osds_in_chunks(self, osd_id_nodes: list[OSDIdNode], batch_size: int = 0, wait: bool = False) -> None:
        if not osd_id_nodes:
            LOGGER.info("No osd ids passed, skipping")
            return

        start_time = datetime.now()
        timeout = timedelta(hours=5)

        if batch_size == 0:
            batch_size = len(osd_id_nodes)

        chunk_start = 0

        def info(msg, *args):
            LOGGER.info(f"[%d/%d osds] {msg}", chunk_start, len(osd_id_nodes), *args)

        for chunk_start in range(0, len(osd_id_nodes), batch_size):
            chunk_num = chunk_start // batch_size
            next_chunk = osd_id_nodes[chunk_start : chunk_start + batch_size]
            info(
                "Undraining osd batch %d: %s",
                chunk_num + 1,
                str(next_chunk),
            )
            self.undrain_osd_id_nodes(osd_id_nodes=next_chunk)
            if wait:
                info("Waiting for the cluster to shift data around...")
                # give some time for the cluster to start shifting things around
                while not self.wait_for_rebalance(timeout=timeout):
                    info("Rebalancing has not started yet, sleeping another 10s for the rebalance to start")
                    time.sleep(10)

        chunk_start = len(osd_id_nodes)
        end_time = datetime.now()
        info("All osds undrained (%s), took %s", osd_id_nodes, (end_time - start_time))

    def undrain_osd_id_nodes(self, osd_id_nodes: list[OSDIdNode]) -> None:
        """Undrains OSD daemons.

        It sets their weight to the number ot TiB of the drive.
        """
        for osd_id_node in osd_id_nodes:
            self.crush_reset_weight_osd(osd_id=osd_id_node.osd_id, node_fqdn=osd_id_node.node_fqdn)
            self.reweight_osd(osd_id=osd_id_node.osd_id, new_weight=1.0)

    def undrain_osds(self, osd_ids: list[int], osd_fqdn: str) -> None:
        """Undrains OSD daemons.

        It sets their weight to whatever the current osds have (or falling back to the number ot TiB of the drive).
        This signature is useful when you have a single node you want to drain from, otherwise prefer
        `undrain_osd_id_nodes`.
        """
        self.undrain_osd_id_nodes([OSDIdNode(osd_id, osd_fqdn) for osd_id in osd_ids])

    def drain_osd_node(
        self,
        osd_host: str,
        be_unsafe: bool = False,
        wait: bool = False,
        batch_size: int = 0,
        osd_ids: list[int] | None = None,
    ) -> None:
        """Given an OSD hostname, depool all it's OSD daemons from the cluster."""
        osds = self.get_host_osds(osd_host=osd_host, in_out=OSDInOut.IN)
        if not osds:
            LOGGER.info("No %s osds found for host %s, skipping...", OSDInOut.IN, osd_host)
            return

        if osd_ids:
            osds = [osd for osd in osds if osd in osd_ids]

        LOGGER.info("Draining IN osds from host %s: %s", osd_host, str(osds))
        self.drain_osds_in_chunks(
            osd_ids=osds,
            batch_size=batch_size,
            be_unsafe=be_unsafe,
            wait=wait,
        )
        LOGGER.info("All osds drained on node %s", osd_host)

    def smart_undrain_osd_nodes(
        self,
        node_fqdns: list[str],
        wait: bool = False,
        batch_size: int = 0,
        osd_ids: list[int] | None = None,
    ) -> None:
        """
        This will depool the given list of OSDIdNodes and sort them so it tries to drain osd daemons from different
        nodes in parallel.
        """
        osd_id_node_pools: list[list[OSDIdNode]] = []
        osd_tree = self.get_osd_tree()
        for node_fqdn in node_fqdns:
            node_host = node_fqdn.split(".", 1)[0]
            node_osd_ids = self.get_host_osds(osd_host=node_host, in_out=OSDInOut.OUT, osd_tree=osd_tree)
            osd_id_node_pools.append(
                [
                    OSDIdNode(osd_id=osd_id, node_fqdn=node_fqdn)
                    for osd_id in node_osd_ids
                    if not osd_ids or osd_id in osd_ids
                ]
            )

        sorted_osd_id_nodes: list[OSDIdNode] = [
            osd_id_node for osd_id_node in round_robin(*osd_id_node_pools) if osd_id_node is not None
        ]

        self.undrain_osds_in_chunks(osd_id_nodes=sorted_osd_id_nodes, batch_size=batch_size, wait=wait)

    def remove_crush_bucket(self, bucket_name: str) -> None:
        """Remove a CRUSH bucket (host/rack/...).

        Note that it will fail if it's not empty already, see destroy_osd for osd entries instead.
        """
        response = self.run_raw(
            "osd",
            "crush",
            "remove",
            bucket_name,
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )

        if "removed item" not in response:
            raise CephException(f"Got unexpected output while remove crush bucket {bucket_name}: {response}")

    def destroy_osd(self, osd_id: int, be_unsafe: bool = False) -> None:
        """Destroys an OSD daemon

        Does it by removing it from the crush table, does not zap the device on the OSD host (that is done when
        re-adding/bootstrapping).
        """
        if not be_unsafe:
            # last check just to make sure
            failures = self.check_osds_safe_to_destroy(osd_ids=[osd_id])
            if failures:
                raise CephException(
                    f"Destroying the osd {osd_id} will put the cluster in an unstable state, if you are sure call "
                    "this function again with `be_unsafe=True`: "
                    "\n".join(failures)
                )

        response = self.run_raw(
            "osd",
            "purge",
            str(osd_id),
            "--yes-i-really-mean-it",
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )

        if f"purged osd.{osd_id}" not in response:
            raise CephException(f"Got unexpected output while purging osd {osd_id}: {response}")

    def get_host_osds(
        self, osd_host: str, in_out: OSDInOut = OSDInOut.ALL, osd_tree: OSDTree | None = None
    ) -> list[int]:
        """Retrieve the list of osd ids that are there in a host (from the ceph cluster rbdmap)."""
        if not osd_tree:
            osd_tree = self.get_osd_tree()
        hosts = list(osd_tree.get_nodes_by_type(wanted_type="host"))

        for host in hosts:
            if host.name == osd_host:
                return [
                    osd.node_id
                    for osd in host.children
                    if (in_out == OSDInOut.OUT and (osd.crush_weight == 0 or osd.reweight == 0))
                    or (in_out == OSDInOut.IN and osd.crush_weight != 0 and osd.reweight != 0)
                    or in_out == OSDInOut.ALL
                ]

        raise CephException(f"Unable to find osd host {osd_host} on: {hosts}")

    def check_osds_ok_to_stop(self, osd_ids: list[int]) -> list[str]:
        """Check if the given OSD daemons can be stopped without affecting the cluster.

        Returns a list of failures/reasons if they are not. An empty list otherwise.
        """
        if not osd_ids:
            return ["No osd_ids passed"]

        result = self.run_raw(
            "osd",
            "ok-to-stop",
            *[str(osd_id) for osd_id in osd_ids],
            cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
            capture_errors=True,
        )
        # Check return output for Octopus version:
        if "are ok to stop without reducing availability or risking data" in result:
            return []
        # Check return output for Pacific and later:
        if '"ok_to_stop":true' in result:
            return []

        return [result]

    def check_osds_safe_to_destroy(self, osd_ids: list[int]) -> list[str]:
        """Check if the given OSD daemons can be destroyed without affecting the cluster.

        Returns a list of failures/reasons if they are not. An empty list otherwise.
        """
        result = self.run_formatted_as_dict(
            "osd", "safe-to-destroy", *[str(osd_id) for osd_id in osd_ids], cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT
        )
        # if there has been enough time between the osds being down they will go to missing_stats
        if set(result["safe_to_destroy"]).union(set(result["missing_stats"])) == set(osd_ids):
            return []

        return [
            (
                "Some osds are not safe to destroy, you can retry with the ones that are safe only or make sure to "
                f"depool/stop the ones that are active: {result}"
            ),
        ]

    def check_if_osd_ready_for_bootstrap(self, osd_controller: CephOSDNodeController) -> list[str]:
        """Check if a node is ready to be added as osd to the cluster.

        Returns a list of any failures that happened.
        """
        failures: list[str] = []

        LOGGER.info(
            "Checking that jumbo frames are allowed to all other nodes in the cluster...",
        )
        if not osd_controller.check_jumbo_frames():
            failures.append("Ping checks failed, see output for details")
            LOGGER.info("    NOOK")

        LOGGER.info("    OK")

        LOGGER.info("Checking that we have the right amount of drives in the host...")
        host_devices = osd_controller.do_lsblk()
        if self.os_hw_raid:
            total_expected_devices = 1 + self.expected_osd_drives_per_host
        else:
            total_expected_devices = OSD_EXPECTED_OS_DRIVES + self.expected_osd_drives_per_host
        if len(host_devices) != total_expected_devices:
            LOGGER.info("    NOOK")
            failures.append(
                f"The host has {len(host_devices)}, when we are expecting {total_expected_devices} "
                f"({self.expected_osd_drives_per_host} for osds, and {OSD_EXPECTED_OS_DRIVES} for the os)"
            )
        else:
            LOGGER.info("    OK")

        LOGGER.info("Checking that we have enough free drives in the host...")
        available_devices = osd_controller.get_available_devices()
        if len(available_devices) > self.expected_osd_drives_per_host:
            LOGGER.info("    NOOK")
            failures.append(
                f"We expected to have at least {OSD_EXPECTED_OS_DRIVES} drives reserved for OS, but it seems we "
                f"would use some of them ({available_devices}), maybe the raid is not properly setup?"
            )
        else:
            LOGGER.info("    OK")

        LOGGER.info("Checking that we have enough OS dedicated drives in the host...")
        # example of soft-raid device:
        # {"name":"sda", "maj:min":"8:0", "rm":false, "size":"447.1G", "ro":false, "type":"disk", "mountpoint":null,
        #    "children": [
        #       {"name":"sda1", ...},
        #       {"name":"sda2", ...
        #          "children": [
        #             {"name":"md0", ...
        #                "children": [
        #                   {"name":"vg0-swap", ...},
        #                   {"name":"vg0-root", ...},
        #                   {"name":"vg0-srv", ...}
        #                ]
        #             }
        #          ]
        #       }
        #    ]
        # },
        if self.os_hw_raid:
            os_devices = [
                device
                for device in host_devices
                if device.get("children", [])
                and any(child.get("mountpoint", "") == "/" for child in device["children"])
            ]
            if len(os_devices) != 1:
                LOGGER.info("    NOOK")
                failures.append(
                    "It seems we don't have the expected os volume. With "
                    "os-hw-raid we expect one os volume, "
                    f"but got {os_devices}"
                )
            else:
                LOGGER.info("    OK")
        else:
            devices_with_soft_raid_on_them = [
                device
                for device in host_devices
                if device.get("children", [])
                and any(
                    child.get("children", []) and child["children"] and child["children"][0].get("name", "") == "md0"
                    for child in device["children"]
                )
            ]
            if len(devices_with_soft_raid_on_them) != OSD_EXPECTED_OS_DRIVES:
                LOGGER.info("    NOOK")
                failures.append(
                    "It seems we don't have the expected raids setup on the OS devices, I was expecting "
                    f"{OSD_EXPECTED_OS_DRIVES} setup in software raid, but got {devices_with_soft_raid_on_them}"
                )
            else:
                LOGGER.info("    OK")

        return failures

    def is_osd_host_valid(self, osd_tree: OSDTree, hostname: str) -> bool:
        """Validates a specific hostname in a given OSD tree.

        It checks that the hostname is present in the tree, and it has the expected attributes.
        """
        found_host_nodes = []
        for host in osd_tree.get_nodes_by_type(wanted_type="host"):
            if host.name == hostname:
                found_host_nodes.append(host)

        if len(found_host_nodes) != 1:
            LOGGER.warning(
                "Expected 1 node in the OSD tree with name='%s' but found %d", hostname, len(found_host_nodes)
            )
            return False

        if len(found_host_nodes[0].children) != self.expected_osd_drives_per_host:
            LOGGER.warning(
                "Expected %d OSDs in the OSD tree for host '%s' but found %d",
                self.expected_osd_drives_per_host,
                hostname,
                len(found_host_nodes[0].children),
            )
            return False

        return True


# Poor man's namespace to compensate for the restriction to not create modules
@dataclass(frozen=True)
class CephTestUtils(UtilsForTesting):
    """Utils to test ceph related code."""

    @staticmethod
    def get_status_dict(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Generate a stub status dict to use when creating CephStatus"""
        status_dict: dict[str, Any] = {"health": {"status": {}, "checks": {}}}

        def _merge_dict(to_update, source_dict):
            if not source_dict:
                return

            for key, value in source_dict.items():
                if key in to_update and isinstance(value, dict):
                    _merge_dict(to_update[key], value)
                else:
                    to_update[key] = value

        _merge_dict(to_update=status_dict, source_dict=overrides)
        return status_dict

    @classmethod
    def get_maintenance_status_dict(cls):
        """Generate a stub maintenance status dict to use when creating CephStatus"""
        maintenance_status_dict = {
            "health": {
                "status": "HEALTH_WARN",
                "checks": {"OSDMAP_FLAGS": {"summary": {"message": "noout,norebalance flag(s) set"}}},
            }
        }

        return cls.get_status_dict(maintenance_status_dict)

    @classmethod
    def get_ok_status_dict(cls):
        """Generate a stub maintenance status dict to use when creating CephStatus"""
        ok_status_dict = {"health": {"status": "HEALTH_OK"}}

        return cls.get_status_dict(ok_status_dict)

    @classmethod
    def get_warn_status_dict(cls):
        """Generate a stub maintenance status dict to use when creating CephStatus"""
        warn_status_dict = {"health": {"status": "HEALTH_WARN"}}

        return cls.get_status_dict(warn_status_dict)

    @staticmethod
    def get_available_device(
        name: str = "sddummy_non_matching_part",
        device_type: str = "disk",
        children: list[Any] | None = None,
        mountpoint: str | None = None,
    ) -> dict[str, Any]:
        """Get a device that is considered available.

        If you pass any value, it will not ensure that it's still considered available.
        """
        available_device: dict[str, Any] = {"name": name, "type": device_type}
        if children is not None:
            available_device["children"] = children

        if mountpoint is not None:
            available_device["mountpoint"] = mountpoint

        return available_device


def get_mon_nodes(cluster_name: CephClusterName) -> list[str]:
    """Get the list of mon nodes given a cluster."""
    return get_nodes_by_role(cluster_name, role_name=CephNodeRoleName.MON)


def get_osd_nodes(cluster_name: CephClusterName) -> list[str]:
    """Get the list of osd nodes given a cluster."""
    return get_nodes_by_role(cluster_name, role_name=CephNodeRoleName.OSD)


def get_node_cluster_name(node: str) -> CephClusterName:
    """Wrapper casting to the right type."""
    return cast(CephClusterName, generic_get_node_cluster_name(node))
