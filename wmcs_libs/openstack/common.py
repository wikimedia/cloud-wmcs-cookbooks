#!/usr/bin/env python3
# pylint: disable=too-many-arguments,too-many-lines
"""Openstack generic related code."""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, NamedTuple, Type, Union, cast

import yaml
from cumin.transports import Command
from spicerack.decorators import retry
from spicerack.remote import Remote, RemoteHosts

from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    ArgparsableEnum,
    CommandRunnerMixin,
    CuminParams,
    OutputFormat,
    run_one_formatted,
    run_one_raw,
    simple_create_file,
)
from wmcs_libs.inventory import generic_get_node_cluster_name, get_node_inventory_info, get_nodes_by_role
from wmcs_libs.inventory.openstack import OpenstackClusterName, OpenstackNodeRoleName

LOGGER = logging.getLogger(__name__)
AGGREGATES_FILE_PATH = "/etc/wmcs_host_aggregates.yaml"
MINUTES_IN_HOUR = 60
SECONDS_IN_MINUTE = 60


OpenstackID = str
OpenstackName = str
# For some reason python 3.9 does not like using `|` for aliases
OpenstackIdentifier = Union[OpenstackID, OpenstackName]


def get_control_nodes(cluster_name: OpenstackClusterName) -> list[str]:
    """Get all the FQDNs of the control nodes (in the future with netbox or similar)."""
    return get_nodes_by_role(cluster_name, role_name=OpenstackNodeRoleName.CONTROL)


def get_control_nodes_from_node(node: str) -> list[str]:
    """Get all the FQDNs of the control nodes from the cluster a given a node is part of."""
    return get_control_nodes(cluster_name=get_node_cluster_name(node))


def get_gateway_nodes(cluster_name: OpenstackClusterName) -> list[str]:
    """Get all the FQDNs of the gateway nodes (in the future with netbox or similar)."""
    return get_nodes_by_role(cluster_name, role_name=OpenstackNodeRoleName.GATEWAY)


def _quote(mystr: str) -> str:
    """Wraps the given string in single quotes."""
    return f"'{mystr}'"


def wait_for_it(
    condition_fn: Callable[..., bool],
    condition_name_msg: str,
    when_failed_raise_exception: Type[Exception],
    condition_failed_msg_fn: Callable[..., str],
    timeout_seconds: int = 900,
):
    """Wait until a condition happens.

    It will call the callable until it returns True, or timeout_seconds passed, in which case it will raise
    when_failed_raise_exception with the return value of condition_failed_msg_fn.
    """
    check_interval_seconds = 10
    start_time = time.time()
    cur_time = start_time
    while cur_time - start_time < timeout_seconds:
        if condition_fn():
            return

        LOGGER.info(
            "'%s' failed, waiting another %ds (timeout=%ds, %ds elapsed)...",
            condition_name_msg,
            check_interval_seconds,
            timeout_seconds,
            cur_time - start_time,
        )

        time.sleep(check_interval_seconds)
        cur_time = time.time()

    raise when_failed_raise_exception(
        f"Waited {timeout_seconds} for {condition_name_msg}, but it never happened:\n" f"{condition_failed_msg_fn()}"
    )


class OpenstackError(Exception):
    """Parent class for all openstack related errors."""


class OpenstackNotFound(OpenstackError):
    """Thrown when trying to get an element from Openstack gets no results."""


class OpenstackMigrationError(OpenstackError):
    """Thrown when there's an issue with migration."""


class OpenstackBadQuota(OpenstackError):
    """Thrown when the quota given is not known or incorrect."""


class OpenstackRuleDirection(ArgparsableEnum):
    """Direction for the security group rule."""

    INGRESS = auto()
    EGRESS = auto()


class OpenstackQuotaName(Enum):
    """Known quota names"""

    BACKUP_GIGABYTES = "backup-gigabytes"
    BACKUPS = "backups"
    CORES = "cores"
    FIXED_IPS = "fixed-ips"
    FLOATING_IPS = "floating-ips"
    GIGABYTES = "gigabytes"
    GIGABYTES_STANDARD = "gigabytes_standard"
    GROUPS = "groups"
    INJECTED_FILE_SIZE = "injected-file-size"
    INJECTED_FILES = "injected-files"
    INJECTED_PATH_SIZE = "injected-path-size"
    INSTANCES = "instances"
    KEY_PAIRS = "key-pairs"
    NETWORKS = "networks"
    PER_VOLUME_GIGABYTES = "per-volume-gigabytes"
    PORTS = "ports"
    PROPERTIES = "properties"
    RAM = "ram"
    RBAC_POLICIES = "rbac_policies"
    ROUTERS = "routers"
    SECGROUP_RULES = "secgroup-rules"
    SECGROUPS = "secgroups"
    SERVER_GROUP_MEMBERS = "server-group-members"
    SERVER_GROUPS = "server-groups"
    SNAPSHOTS = "snapshots"
    SNAPSHOTS_STANDARD = "snapshots_standard"
    SUBNET_POOLS = "subnet_pools"
    SUBNETS = "subnets"
    VOLUMES = "volumes"
    VOLUMES_STANDARD = "volumes_standard"


class Unit(Enum):
    """Basic information storage units."""

    GIGA = "G"
    MEGA = "M"
    KILO = "K"
    UNIT = "B"

    def next_unit(self) -> "Unit":
        """Decreases the given unit by one order of magnitude."""
        if self == Unit.GIGA:
            return Unit.MEGA
        if self == Unit.MEGA:
            return Unit.KILO
        if self == Unit.KILO:
            return Unit.UNIT

        raise OpenstackBadQuota(f"Unit {self} can't be lowered.")


class OpenstackQuotaEntry(NamedTuple):
    """Represents a specific entry for a quota."""

    name: OpenstackQuotaName
    value: int

    def to_cli(self) -> str:
        """Return the openstack cli equivalent of setting this quota entry."""
        return f"--{self.name.value.lower().replace('_', '-')}={self.value}"

    def __str__(self):
        """Convert a OpenstackQuotaEntry to a formatted string for display."""
        return f"{self.value} {self.name.value}"

    @classmethod
    def from_human_spec(cls, name: OpenstackQuotaName, human_spec: str) -> "OpenstackQuotaEntry":
        """Given a human spec (ex. 10G) and a quota name gives a quota entry with the right value."""
        return cls(
            name=name,
            value=cls._human_to_quota_number(
                human_spec=human_spec,
                quota_name=name,
            ),
        )

    @staticmethod
    def _human_to_quota_number(human_spec: str, quota_name: OpenstackQuotaName) -> int:
        """Maps from human strings (ex. 10G) to the string needed for the given quota.

        This is to be able to translate "add 10G of ram" to the number that openstack expects for the ram, that is
        megabytes.
        """
        if "gigabytes" in quota_name.value:
            dst_unit = Unit.GIGA
        elif quota_name == OpenstackQuotaName.RAM:
            dst_unit = Unit.MEGA
        else:
            dst_unit = Unit.UNIT

        try:
            int(human_spec[-1:])
            # if no unit passed use the openstack default one
            cur_unit = dst_unit
            cur_value = int(human_spec)

        except ValueError as error:
            unit_match = re.match("([0-9]+)([^0-9]+)$", human_spec)
            if not unit_match:
                raise ValueError(f"Unable to parse human spec '{human_spec}'") from error

            value_str, unit_str = unit_match.groups()
            # we only care about the first char, ex. GB -> G
            cur_unit = Unit(unit_str[0].upper())
            cur_value = int(value_str)

        while dst_unit != cur_unit:
            cur_value *= 1024
            try:
                cur_unit = cur_unit.next_unit()
            except OpenstackBadQuota as error:
                raise OpenstackBadQuota(
                    f"Unable to translate {human_spec} for {quota_name} (maybe the quota chosen does not support that "
                    "unit?)"
                ) from error

        return cur_value


class OpenstackServerGroupPolicy(ArgparsableEnum):
    """Affinity for the server group."""

    SOFT_ANTI_AFFINITY = "soft-anti-affinity"
    ANTI_AFFINITY = "anti-affinity"
    AFFINITY = "affinity"
    SOFT_AFFINITY = "soft-affinity"


class NeutronAgentType(Enum):
    """list of neutron agent types and their 'agent type' string.

    Extracted from 'wmcs-openstack network agent list' on a full installation. Note that they are case sensitive.
    """

    L3_AGENT = "L3 agent"
    OVS_AGENT = "Open vSwitch agent"
    LINUX_BRIDGE_AGENT = "Linux bridge agent"
    DHCP_AGENT = "DHCP agent"
    METADATA_AGENT = "Metadata agent"

    @property
    def openstack_id(self) -> str:
        """The short name used in OpenStack CLI commands for filtering."""
        if self == NeutronAgentType.L3_AGENT:
            return "l3"
        if self == NeutronAgentType.OVS_AGENT:
            return "open-vswitch"
        if self == NeutronAgentType.LINUX_BRIDGE_AGENT:
            return "linux-bridge"
        if self == NeutronAgentType.DHCP_AGENT:
            return "dhcp"
        if self == NeutronAgentType.METADATA_AGENT:
            return "metadata"
        raise ValueError(f"Unknown agent type '{self}'!")


class NeutronAgentHAState(Enum):
    """HA state for a neutron agent."""

    ACTIVE = "active"
    STANDBY = "standby"


@dataclass(frozen=True)
class NeutronPartialAgent:
    """Represents the details of a Neutron agent that can be seen in 'openstack network agent list' output."""

    agent_id: OpenstackID
    agent_type: NeutronAgentType
    host: str
    availability_zone: str | None
    alive: bool
    admin_state_up: bool
    binary: str

    @classmethod
    def from_agent_data(cls, agent_data: dict[str, Any]) -> "NeutronPartialAgent":
        return cls(
            agent_id=agent_data["ID"],
            agent_type=NeutronAgentType(agent_data["Agent Type"]),
            host=agent_data["Host"],
            availability_zone=agent_data["Availability Zone"],
            alive=agent_data["Alive"],
            admin_state_up=agent_data["State"],
            binary=agent_data["Binary"],
        )


@dataclass(frozen=True)
class NeutronAgentWithHAState(NeutronPartialAgent):
    """Represents a Neutron agent with a known HA status."""

    ha_state: NeutronAgentHAState

    @classmethod
    def from_agent_data(cls, agent_data: dict[str, Any]) -> "NeutronAgentWithHAState":
        return cls(
            agent_id=agent_data["ID"],
            agent_type=NeutronAgentType(agent_data["Agent Type"]),
            host=agent_data["Host"],
            availability_zone=agent_data["Availability Zone"],
            alive=agent_data["Alive"],
            admin_state_up=agent_data["State"],
            binary=agent_data["Binary"],
            ha_state=NeutronAgentHAState(agent_data["HA State"]),
        )


class NeutronRouterStatus(Enum):
    """Status of a neutron router.

    Gotten from https://github.com/openstack/neutron-lib/blob/master/neutron_lib/constants.py#L427
    """

    ACTIVE = "ACTIVE"
    ALLOCATING = "ALLOCATING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class NeutronPartialRouter:
    """Represents the details of a Neutron router that can be seen in 'openstack router list' output.

    We are only storing the fields we are using, if you need more please add them.
    """

    name: str
    router_id: OpenstackID
    tenant_id: OpenstackID
    has_ha: bool
    status: NeutronRouterStatus
    admin_state_up: bool

    @classmethod
    def from_router_data(cls, data: dict[str, Any]) -> "NeutronPartialRouter":
        """Creates a NeutronPartialRouter from the json output of 'openstack router list'.

        Note that we only get the fields we use/find useful, add new whenever needed.

        Example of list_data:
        {
            "ID": "d93771ba-2711-4f88-804a-8df6fd03978a",
            "Name": "cloudinstances2b-gw",
            "Status": "ACTIVE",
            "State": true,
            "Project": "admin",
            "Distributed": false,
            "HA": true
        }
        """
        return cls(
            router_id=data["ID"],
            name=data["Name"],
            tenant_id=data["Project"],
            has_ha=data["HA"],
            status=NeutronRouterStatus(data["Status"]),
            admin_state_up=data["State"],
        )

    def __str__(self) -> str:
        """Return the string representation of this class."""
        return f"{self.name}: router_id:{self.router_id} tenant_id:{self.tenant_id} status:{self.status} has_ha:{self.has_ha}"  # noqa: E501

    def is_healthy(self) -> bool:
        """Given a router, check if it's up."""
        return self.status == NeutronRouterStatus.ACTIVE and self.has_ha and self.admin_state_up


@dataclass(frozen=True)
class NeutronPartialPort:
    """Represents the details of a Neutron port that can be seen in 'openstack port list' output."""

    port_id: OpenstackID
    port_name: str
    mac_address: str

    @classmethod
    def from_port_data(cls, port_data: dict[str, Any]) -> "NeutronPartialPort":
        return cls(
            port_id=port_data["ID"],
            port_name=port_data["Name"],
            mac_address=port_data["MAC Address"],
        )


class OpenstackAPI(CommandRunnerMixin):
    """Class to interact with the Openstack API (indirectly for now)."""

    def __init__(
        self,
        remote: Remote,
        cluster_name: OpenstackClusterName = OpenstackClusterName.EQIAD1,
        project: OpenstackName = "",
    ):
        """Init."""
        self.project = project
        self.cluster_name = cluster_name
        self.control_node_fqdn = get_control_nodes(cluster_name)[0]
        self.control_node = remote.query(f"D{{{self.control_node_fqdn}}}", use_sudo=True)
        super().__init__(command_runner_node=self.control_node)

    def _get_full_command(self, *command: str, json_output: bool = True, project_as_arg: bool = False):
        # some commands don't have formatted output
        if json_output:
            format_args = ["-f", "json"]
        else:
            format_args = []
        if "delete" in command:
            format_args = []

        if "--os-cloud" not in command:
            oscloud_args = ["--os-cloud", "novaadmin"]
        else:
            oscloud_args = []

        # some commands require passing the project as an argument and cannot use OS_PROJECT_ID
        if project_as_arg:
            return ["wmcs-openstack", *command, self.project, *format_args, *oscloud_args]

        return ["env", f"OS_PROJECT_ID={self.project}", "wmcs-openstack", *command, *format_args, *oscloud_args]

    def hypervisor_list(self, cumin_params: CuminParams | None = None) -> list[dict[str, Any]]:
        """Returns a list of hypervisors."""
        return self.run_formatted_as_list(
            "hypervisor",
            "list",
            "--long",
            "--sort-descending",
            cumin_params=CuminParams.as_safe(cumin_params),
        )

    def get_nodes_domain(self) -> str:
        """Return the domain of the cluster handled by this controller."""
        info = get_node_inventory_info(node=self.control_node_fqdn)
        return f"{info.site_name.value}.wmnet"

    def create_service_ip(self, ip_name: OpenstackName, network: OpenstackIdentifier) -> dict[str, Any]:
        """Create a service IP with a specified name"""
        return self.run_formatted_as_dict("port", "create", "--network", _quote(network), _quote(ip_name))

    def attach_service_ip(self, ip_address: str, server_port_id: OpenstackIdentifier) -> str:
        """Attach a specified service ip address to the specified port"""
        return self.run_raw(
            "port",
            "set",
            "--allowed-address",
            f"ip-address={ip_address}",
            _quote(server_port_id),
            json_output=False,
        )

    def detach_service_ip(self, ip_address: str, mac_addr: str, server_port_id: OpenstackIdentifier) -> str:
        """Detach a specified service ip address from the specified port"""
        return self.run_raw(
            "port",
            "unset",
            "--allowed-address",
            f"ip-address={ip_address},mac-address={mac_addr}",
            _quote(server_port_id),
            json_output=False,
        )

    def get_nova_services(self) -> list[dict[str, Any]]:
        """Return nova's list of registered services"""
        return self.run_formatted_as_list("compute", "service", "list", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)

    def get_designate_services(self) -> list[dict[str, Any]]:
        """Return designate's list of registered services"""
        return self.run_formatted_as_list("dns", "service", "list", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)

    def get_neutron_agents(
        self, *, host: str | None = None, agent_type: NeutronAgentType | None = None
    ) -> list[NeutronPartialAgent]:
        """Return neutron's list of registered services"""
        filter_args = []
        if host:
            filter_args.append(f"--host={host}")
        if agent_type:
            filter_args.append(f"--agent-type={agent_type.openstack_id}")

        data = self.run_formatted_as_list(
            "network", "agent", "list", *filter_args, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT
        )
        return [NeutronPartialAgent.from_agent_data(agent) for agent in data]

    def neutron_agent_set_admin_up(self, agent_id: OpenstackID) -> None:
        """Set the given agent as admin-state-up (online)."""
        self.run_raw(
            "network", "agent", "set", "--enable", agent_id, json_output=False, cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT
        )

    def neutron_agent_set_admin_down(self, agent_id: OpenstackID) -> None:
        """Set the given agent as admin-state-down (offline)."""
        self.run_raw(
            "network",
            "agent",
            "set",
            "--disable",
            agent_id,
            json_output=False,
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )

    def get_neutron_agents_for_router(self, router_id: OpenstackIdentifier) -> list[NeutronAgentWithHAState]:
        data = self.run_formatted_as_list(
            "network", "agent", "list", "--long", f"--router={router_id}", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT
        )
        return [NeutronAgentWithHAState.from_agent_data(agent) for agent in data]

    def get_routers(self) -> list[NeutronPartialRouter]:
        """Return neutron's list of registered services"""
        data = self.run_formatted_as_list("router", "list", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)
        return [NeutronPartialRouter.from_router_data(router) for router in data]

    def get_cinder_services(self) -> list[dict[str, Any]]:
        """Return cinder's list of registered services"""
        return self.run_formatted_as_list("volume", "service", "list", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)

    def _port_get(self, port_filter: list[str]) -> list[NeutronPartialPort]:
        data = self.run_formatted_as_list("port", "list", *port_filter, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)
        return [NeutronPartialPort.from_port_data(port) for port in data]

    def port_get_for_server(self, server_id: OpenstackID) -> list[NeutronPartialPort]:
        """Get ports for a specified server."""
        return self._port_get(port_filter=[f'--server="{server_id}"'])

    def port_get_by_ip(self, ip_address: str) -> list[NeutronPartialPort]:
        """Get ports for specified IP address"""
        return self._port_get(port_filter=[f'--fixed-ip="ip-address={ip_address}"'])

    def zone_get(self, name) -> list[dict[str, Any]]:
        """Get zone record for specified dns zone"""
        return self.run_formatted_as_list("zone", "list", "--name", name, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)

    def recordset_create(self, zone_id, record_type, name, record) -> dict[str, Any]:
        """Get zone record for specified dns zone"""
        return self.run_formatted_as_dict(
            "recordset", "create", "--type", record_type, "--record", record, zone_id, name
        )

    def server_show(self, vm_name: OpenstackIdentifier) -> dict[str, Any]:
        """Get the information for a VM."""
        return self.run_formatted_as_dict("server", "show", vm_name, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)

    def server_list(self, long: bool = False, cumin_params: CuminParams | None = None) -> list[dict[str, Any]]:
        """Retrieve the list of servers for the project."""
        _long = "--long" if long else ""
        return self.run_formatted_as_list("server", "list", _long, cumin_params=CuminParams.as_safe(cumin_params))

    def server_list_filter_exists(self, hostnames: list[str], cumin_params: CuminParams | None = None) -> list[str]:
        """Verify if all servers in the list exists.

        Returns the input list filtered with those hostnames that do exists.
        """
        listing = self.server_list(cumin_params=cumin_params)

        for hostname in hostnames:
            if not any(info for info in listing if info["Name"] == hostname):
                hostnames.remove(hostname)

        return hostnames

    def server_exists(self, hostname: str, cumin_params: CuminParams | None = None) -> bool:
        """Returns True if a server exists, False otherwise."""
        listing = self.server_list(cumin_params=cumin_params)

        if not any(info for info in listing if info["Name"] == hostname):
            return False

        return True

    def server_delete(self, name_to_remove: OpenstackName) -> None:
        """Delete a server.

        Note that the name_to_remove is the name of the node as registered in
        Openstack, that's probably not the FQDN (and hopefully the hostname,
        but maybe not).
        """
        self.run_raw("server", "delete", name_to_remove)

    def server_force_reboot(self, name_to_reboot: OpenstackName) -> None:
        """Force reboot a VM.

        Note that the name_to_reboot is the name of the VM as registered in
        Openstack, that's probably not the FQDN (and hopefully the hostname,
        but maybe not).
        """
        self.run_raw("server", "reboot", "--hard", name_to_reboot, json_output=False)

    @retry(
        tries=16,
        backoff_mode="power",
        failure_message="Server is in unexpected status",
        exceptions=(OpenstackError,),
    )
    def _server_wait_for_state(self, server: OpenstackIdentifier, states: Collection[str]) -> None:
        """Wait for a server to be in a specific state."""
        # TODO: should states be an Enum here?
        server_state = self.server_show(server).get("status")
        if server_state not in states:
            raise OpenstackError(f"Server status is '{server_state}', not in any of {', '.join(states)}")

    def server_start(self, server: OpenstackIdentifier):
        """Start a server."""
        self.run_raw("server", "start", server, json_output=False)
        self._server_wait_for_state(server=server, states=["ACTIVE"])

    def server_stop(self, server: OpenstackIdentifier):
        """Stop a server."""
        self.run_raw("server", "stop", server, json_output=False)
        self._server_wait_for_state(server=server, states=["SHUTOFF"])

    def server_resize(self, server: OpenstackIdentifier, new_flavor_name: OpenstackName) -> None:
        """Resizes a server to a given flavor."""
        orig_status = self.server_show(server).get("status")
        self.run_raw("server", "resize", "--flavor", new_flavor_name, server, json_output=False)
        self._server_wait_for_state(server=server, states=["VERIFY_RESIZE"])
        self.run_raw("server", "resize", "confirm", server, json_output=False)
        self._server_wait_for_state(server=server, states=[orig_status])

    def volume_create(self, name: OpenstackName, size: int) -> str:
        """Create a volume and return the ID of the created volume.

        --size is in GB
        """
        out = self.run_formatted_as_dict("volume", "create", "--size", str(size), "--type", "standard", name)
        return out["id"]

    def volume_attach(self, server_id: OpenstackID, volume_id: OpenstackID) -> None:
        """Attach a volume to a server"""
        self.run_raw("server", "add", "volume", server_id, volume_id, json_output=False)

    def volume_detach(self, server_id: OpenstackID, volume_id: OpenstackID) -> None:
        """Attach a volume to a server"""
        self.run_raw("server", "remove", "volume", server_id, volume_id, json_output=False)

    def server_from_id(self, server_id: OpenstackIdentifier) -> dict[str, Any]:
        """Given the ID of a server, return the server details"""
        return self.run_formatted_as_dict("server", "show", server_id, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)

    def volume_from_id(self, volume_id: OpenstackIdentifier) -> dict[str, Any]:
        """Given the ID of a volume, return the volume details"""
        return self.run_formatted_as_dict("volume", "show", volume_id, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)

    def server_create(
        self,
        name: OpenstackName,
        flavor: OpenstackIdentifier,
        image: OpenstackIdentifier,
        network: OpenstackIdentifier,
        server_group_id: OpenstackID | None = None,
        security_group_ids: list[OpenstackID] | None = None,
        properties: dict[str, str] | None = None,
        availability_zone: str | None = None,
    ) -> OpenstackIdentifier:
        """Create a server and return the ID of the created server.

        Note: You will probably want to add the server to the 'default' security group at least.
        """
        security_group_options = []
        if security_group_ids:
            for security_group_id in security_group_ids:
                security_group_options.extend(["--security-group", security_group_id])

        server_group_options = []
        if server_group_id:
            server_group_options.extend(["--hint", f"group={server_group_id}"])

        properties_opt = []
        if properties:
            for i in properties:
                properties_opt.extend(["--property", f"{i}='{properties[i]}'"])

        availability_zone_opt = []
        if availability_zone:
            availability_zone_opt.extend(["--availability-zone", availability_zone])

        out = self.run_formatted_as_dict(
            "server",
            "create",
            "--flavor",
            _quote(flavor),
            "--image",
            _quote(image),
            "--network",
            _quote(network),
            "--wait",
            *server_group_options,
            *security_group_options,
            *properties_opt,
            *availability_zone_opt,
            name,
        )
        return out["id"]

    def server_get_aggregates(self, name: OpenstackName) -> list[dict[str, Any]]:
        """Get all the aggregates for the given server."""
        # NOTE: this currently does a bunch of requests making it slow, can be simplified
        # once the following gets released:
        #  https://review.opendev.org/c/openstack/python-openstackclient/+/794237
        current_aggregates = self.aggregate_list(cumin_params=CuminParams(print_output=False))
        server_aggregates: list[dict[str, Any]] = []
        for aggregate in current_aggregates:
            aggregate_details = self.aggregate_show(
                aggregate=aggregate["Name"], cumin_params=CuminParams(print_output=False, print_progress_bars=False)
            )
            if name in aggregate_details.get("hosts", []):
                server_aggregates.append(aggregate_details)

        return server_aggregates

    def security_group_list(self, cumin_params: CuminParams | None = None) -> list[dict[str, Any]]:
        """Retrieve the list of security groups."""
        return self.run_formatted_as_list("security", "group", "list", cumin_params=CuminParams.as_safe(cumin_params))

    def security_group_create(self, name: OpenstackName, description: str) -> None:
        """Create a security group."""
        self.run_raw("security", "group", "create", name, "--description", _quote(description))

    def security_group_rule_create(
        self, direction: OpenstackRuleDirection, remote_group: OpenstackName, security_group: OpenstackName
    ) -> None:
        """Create a rule inside the given security group."""
        self.run_raw(
            "security",
            "group",
            "rule",
            "create",
            f"--{direction.name.lower()}",
            "--remote-group",
            remote_group,
            "--protocol",
            "any",
            security_group,
        )

    def security_group_ensure(
        self, security_group: OpenstackName, description: str = "Security group created from spicerack."
    ) -> None:
        """Make sure that the given security group exists, create it if not there."""
        try:
            self.security_group_by_name(name=security_group, cumin_params=CuminParams(print_output=False))
            LOGGER.info("Security group %s already exists, not creating.", security_group)

        except OpenstackNotFound:
            LOGGER.info("Creating security group %s...", security_group)
            self.security_group_create(name=security_group, description=description)
            self.security_group_rule_create(
                direction=OpenstackRuleDirection.EGRESS, remote_group=security_group, security_group=security_group
            )
            self.security_group_rule_create(
                direction=OpenstackRuleDirection.INGRESS, remote_group=security_group, security_group=security_group
            )

    def security_group_by_name(
        self, name: OpenstackName, cumin_params: CuminParams | None = None
    ) -> dict[str, Any] | None:
        """Retrieve the security group info given a name.

        Raises OpenstackNotFound if there's no security group found for the given name in the current project.
        """
        existing_security_groups = self.security_group_list(cumin_params=cumin_params)
        for security_group in existing_security_groups:
            if security_group["Project"] == self.project:
                if security_group["Name"] == name:
                    return security_group

        raise OpenstackNotFound(f"Unable to find a security group with name {name}")

    def server_group_list(self, cumin_params: CuminParams | None = None) -> list[dict[str, Any]]:
        """Get the list of server groups.

        Note:  it seems that on cli the project flag shows nothing :/ so we get the list all of them.
        """
        return self.run_formatted_as_list("server", "group", "list", cumin_params=CuminParams.as_safe(cumin_params))

    def server_group_create(self, name: OpenstackName, policy: OpenstackServerGroupPolicy) -> None:
        """Create a server group."""
        self.run_raw(
            "--os-compute-api-version=2.15",  # needed to be 2.15 or higher for soft-* policies
            "server",
            "group",
            "create",
            "--policy",
            policy.value,
            name,
        )

    def server_group_ensure(
        self, server_group: OpenstackName, policy: OpenstackServerGroupPolicy = OpenstackServerGroupPolicy.ANTI_AFFINITY
    ) -> None:
        """Make sure that the given server group exists, create it if not there."""
        try:
            self.server_group_by_name(name=server_group, cumin_params=CuminParams(print_output=False))
            LOGGER.info("Server group %s already exists, not creating.", server_group)
        except OpenstackNotFound:
            self.server_group_create(policy=policy, name=server_group)

    def server_group_by_name(
        self, name: OpenstackName, cumin_params: CuminParams | None = None
    ) -> dict[str, Any] | None:
        """Retrieve the server group info given a name.

        Raises OpenstackNotFound if there's no server group found with the given name.
        """
        all_server_groups = self.server_group_list(cumin_params=cumin_params)
        for server_group in all_server_groups:
            if server_group.get("Name", "") == name:
                return server_group

        raise OpenstackNotFound(f"Unable to find a server group with name {name}")

    def aggregate_list(self, cumin_params: CuminParams | None = None) -> list[dict[str, Any]]:
        """Get the simplified list of aggregates."""
        return self.run_formatted_as_list("aggregate", "list", "--long", cumin_params=CuminParams.as_safe(cumin_params))

    def aggregate_show(self, aggregate: OpenstackIdentifier, cumin_params: CuminParams | None) -> dict[str, Any]:
        """Get the details of a given aggregate."""
        return self.run_formatted_as_dict(
            "aggregate", "show", aggregate, cumin_params=CuminParams.as_safe(cumin_params)
        )

    def aggregate_remove_host(self, aggregate_name: OpenstackName, host_name: OpenstackName) -> None:
        """Remove the given host from the aggregate."""
        result = self.run_raw(
            "aggregate",
            "remove",
            "host",
            aggregate_name,
            host_name,
            capture_errors=True,
            cumin_params=CuminParams(print_output=False, print_progress_bars=False),
        )
        if "HTTP 404" in result:
            raise OpenstackNotFound(
                f"Node {host_name} was not found in aggregate {aggregate_name}, did you try using the hostname "
                "instead of the fqdn?"
            )

    def aggregate_add_host(self, aggregate_name: OpenstackName, host_name: OpenstackName) -> None:
        """Add the given host to the aggregate."""
        result = self.run_raw("aggregate", "add", "host", aggregate_name, host_name, capture_errors=True)
        if "HTTP 404" in result:
            raise OpenstackNotFound(
                f"Node {host_name} was not found in aggregate {aggregate_name}, did you try using the hostname "
                "instead of the fqdn?"
            )

    def aggregate_persist_on_host(self, host: RemoteHosts, current_aggregates: list[dict[str, Any]]) -> None:
        """Creates a file in the host with its current list of aggregates.

        For later usage, for example, when moving the host temporarily to another aggregate.
        """
        simple_create_file(
            dst_node=host, contents=yaml.dump(current_aggregates, indent=4), remote_path=AGGREGATES_FILE_PATH
        )

    @staticmethod
    def aggregate_load_from_host(host: RemoteHosts) -> list[dict[str, Any]]:
        """Load the persisted list of aggregates from the host."""
        try:
            result = run_one_formatted(
                command=["cat", AGGREGATES_FILE_PATH],
                node=host,
                try_format=OutputFormat.YAML,
                cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
            )

        except Exception as error:
            raise OpenstackNotFound(f"Unable to cat the file {AGGREGATES_FILE_PATH} on host {host}") from error

        if isinstance(result, list):
            return result

        raise TypeError(f"Expected a list, got {result}")

    def drain_hypervisor(self, hypervisor_name: OpenstackName) -> None:
        """Drain a hypervisor."""
        command = Command(
            command=f"bash -c 'source /root/novaenv.sh && wmcs-drain-hypervisor {hypervisor_name}'",
            timeout=SECONDS_IN_MINUTE * MINUTES_IN_HOUR * 2,
        )
        result = run_one_raw(command=command, node=self.control_node)

        if not result:
            raise OpenstackMigrationError(
                f"Got no result when running {command} on {self.control_node_fqdn}, was expecting some output at "
                "least."
            )

    def quota_show(self) -> dict[str | OpenstackQuotaName, Any]:
        """Get the quotas for a project.

        Note that it will cast any known quota names to OpenstackQuotaName enums.
        """
        # OS_PROJECT_ID=PROJECT wmcs-openstack quota show displays the admin project!
        # This must be run as wmcs-openstack quota show PROJECT
        raw_quotas = self.run_formatted_as_list(
            "quota", "show", project_as_arg=True, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT
        )
        final_quotas: dict[str | OpenstackQuotaName, Any] = {}
        for quota_entry in raw_quotas:
            quota_name, quota_value = quota_entry["Resource"], quota_entry["Limit"]
            try:
                quota_entry = OpenstackQuotaEntry(name=OpenstackQuotaName(quota_name), value=quota_value)
                final_quotas[quota_entry.name] = quota_entry

            except ValueError:
                final_quotas[quota_name] = quota_value

        return final_quotas

    def quota_set(self, *quotas: OpenstackQuotaEntry) -> None:
        """Set a quota to the given value.

        Note that this sets the final value, not an increase.
        """
        quotas_cli = [quota.to_cli() for quota in quotas]

        self.run_raw("quota", "set", *quotas_cli, json_output=False, project_as_arg=True)

    def trove_quota_set(self, resource, value) -> None:
        """Set a quota to the given value.

        Note that this sets the final value, not an increase.
        """
        self.run_raw("database", "quota", "update", self.project, resource, value)

    def quota_increase(self, *quota_increases: OpenstackQuotaEntry) -> None:
        """Set a quota to the current value plus the given increase."""
        current_quotas = self.quota_show()

        increased_quotas: list[OpenstackQuotaEntry] = []

        for new_quota in quota_increases:
            if new_quota.name not in current_quotas:
                raise OpenstackError(f"Quota {new_quota} was not found in the remote Openstack API.")

            new_value = new_quota.value + current_quotas[new_quota.name].value
            increased_quotas.append(OpenstackQuotaEntry(name=new_quota.name, value=new_value))

        self.quota_set(*increased_quotas)

        # Validate quota was updated as expected
        new_quotas = self.quota_show()
        for new_quota in increased_quotas:
            if new_quota.value != new_quotas[new_quota.name].value:
                raise OpenstackError(
                    f"{new_quotas[new_quota.name]} quota of {new_quotas[new_quota.name].value} "
                    f"does not match expected value of {new_quota.value}"
                )

    def role_list_assignments(self, user_name: OpenstackName) -> list[dict[str, Any]]:
        """List the assignments for a user in the project."""
        return self.run_formatted_as_list(
            "role",
            "assignment",
            "list",
            f"--project={self.project}",
            f"--user={user_name}",
            cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
        )

    def role_add(self, role_name: OpenstackName, user_name: OpenstackName) -> None:
        """Add a user to a role for a project, it will not fail if the user is already has that role."""
        self.run_raw("role", "add", f"--project={self.project}", f"--user={user_name}", role_name, json_output=False)

    def role_remove(self, role: OpenstackIdentifier, user_name: OpenstackName) -> None:
        """Remove a user from a role for a project, it will not fail if the user is not in that that role."""
        self.run_raw("role", "remove", f"--project={self.project}", f"--user={user_name}", role, json_output=False)

    def project_create(self, project: OpenstackName, description: str) -> None:
        """Creates a new project."""
        self.run_raw("project", "create", "--enable", f"'--description={description}'", project, json_output=False)


def get_node_cluster_name(node: str) -> OpenstackClusterName:
    """Wrapper casting to the specific openstack type."""
    return cast(OpenstackClusterName, generic_get_node_cluster_name(node))
