from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from typing import Literal

from spicerack.remote import Remote, RemoteExecutionError, RemoteHosts

from wmcs_libs.common import CUMIN_SAFE_WITHOUT_OUTPUT, run_one_formatted_as_list, run_one_raw
from wmcs_libs.inventory.dynamic import get_inventory
from wmcs_libs.inventory.libs import get_nodes_by_role
from wmcs_libs.inventory.toolsdb import ToolforgeToolsDBClusterName, ToolforgeToolsDBNodeRoleName

LOGGER = logging.getLogger(__name__)

ReplicationStatus = Literal["Running", "Stopped", "Unknown"]
HostStatus = Literal["Up", "Down", "Unknown"]


@dataclass(frozen=True)
class ReplicationState:
    """Shared data between primary and replica states."""

    status: ReplicationStatus


@dataclass(frozen=True)
class PrimaryReplicationState(ReplicationState):
    """Primary node state information."""

    replica_ids: list[str]


@dataclass(frozen=True)
class ReplicaReplicationState(ReplicationState):
    """Replica node state information.

    To add new ones to be fetched, just add them here as they show in 'show slave status', lowercased and they will
    be populated automatically.
    """

    slave_io_state: str
    master_host: str
    master_log_file: str
    read_master_log_pos: str
    relay_master_log_file: str
    slave_io_running: str
    slave_sql_running: str
    replicate_wild_ignore_table: str
    last_error: str
    last_errno: str
    exec_master_log_pos: str
    master_server_id: str
    seconds_behind_master: str


@dataclass(frozen=True)
class NodeStatus:
    """Info about a toolsdb node."""

    fqdn: str
    nodeid: str
    replication_state: ReplicationState
    host_status: HostStatus
    mariadb_status: str


@dataclass(frozen=True)
class ClusterStatus:
    primary: NodeStatus
    replicas: list[NodeStatus]


@dataclass
class MariaDBNode:
    fqdn: str
    node: RemoteHosts
    role: ToolforgeToolsDBNodeRoleName

    def _run_mysql_raw(self, query: str) -> str:
        return run_one_raw(
            node=self.node,
            command=["mariadb", "--user=root", "-e", f'"{query}"'],
            cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
        )

    def get_mariadb_nodeid(self) -> str:
        nodeid_raw = self._run_mysql_raw(query="show variables like 'server_id' \\G")
        # ugly mysql table output parsing
        nodeid = nodeid_raw.splitlines()[-1].split(":")[-1].strip()
        return nodeid

    def get_connected_replica_ids(self) -> list[str]:
        replicas_raw = self._run_mysql_raw(query="show slave hosts")
        replica_ids: list[str] = []
        # skip the header, note that this is different than using interactive cli
        # as the terminal will show everything boxed with lines |
        # and the non-interactive is tab-separated
        for replica_line in replicas_raw.splitlines()[1:]:
            replica_ids.append(replica_line.split("\t")[0])

        return replica_ids

    def get_host_status(self) -> HostStatus:
        try:
            run_one_raw(node=self.node, command=["uptime"], cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)

        except RemoteExecutionError as error:
            logging.warning("Got error when connecting to %s: %s", self.fqdn, str(error))
            return "Down"

        except Exception as error:  # pylint: disable=broad-except
            logging.warning("Got error when connecting to %s: %s", self.fqdn, str(error))
            return "Unknown"

        return "Up"

    def get_mariadb_status(self) -> str:
        """This returns one of 'Unknown', 'Running' or 'Stopped(<some info>)'."""
        units = run_one_formatted_as_list(
            node=self.node,
            command=["systemctl", "list-units", "--all", "--output=json"],
            capture_errors=True,
            cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
        )
        mariadb_unit = next((unit for unit in units if unit["unit"] == "mariadb.service"), None)
        if mariadb_unit is None:
            logging.warning("Unable to find mariadb.service on host %s", self.fqdn)
            return "Unknown"

        if mariadb_unit["active"] == "active" and mariadb_unit["sub"] == "running":
            return "Running"

        return f"Stopped({mariadb_unit['active']}-{mariadb_unit['sub']})"

    def get_replication_state(self) -> ReplicationState:
        if self.role == ToolforgeToolsDBNodeRoleName.PRIMARY:
            replica_ids = self.get_connected_replica_ids()
            return PrimaryReplicationState(
                status="Running" if len(replica_ids) > 0 else "Stopped",
                replica_ids=replica_ids,
            )

        replica_status_raw = self._run_mysql_raw(query="show slave status \\G")
        params = {}
        existing_fields = [field.name for field in fields(ReplicaReplicationState)]
        for line in replica_status_raw.splitlines():
            if ":" not in line:
                continue

            key, val = line.split(":")[0].strip().lower(), line.split(":")[1].strip()
            if key in existing_fields:
                params[key] = val

        if params.get("slave_io_running", "Unknown") == "Yes" and params.get("slave_sql_running", "Unknown") == "Yes":
            status: ReplicationStatus = "Running"
        elif params.get("slave_io_running", "Unknown") == "No" and params.get("slave_sql_running", "Unknown") == "No":
            status = "Stopped"

        return ReplicaReplicationState(status=status, **params)

    def get_node_status(self) -> NodeStatus:
        host_status = self.get_host_status()
        mariadb_status = "Unknown" if host_status != "Up" else self.get_mariadb_status()
        replication_state = (
            ReplicationState(status="Unknown") if mariadb_status != "Running" else self.get_replication_state()
        )
        nodeid = "Unknown" if mariadb_status != "Running" else self.get_mariadb_nodeid()
        return NodeStatus(
            fqdn=self.fqdn,
            host_status=host_status,
            mariadb_status=mariadb_status,
            replication_state=replication_state,
            nodeid=nodeid,
        )

    def get_binlog_entry(self, logfile: str, start_pos: int, stop_pos: int = -1) -> str:
        if stop_pos < 0:
            stop_pos = start_pos + 1

        return run_one_raw(
            node=self.node,
            command=[
                "mysqlbinlog",
                "--base64-output=decode-rows",
                "--verbose",
                f"--start-position={start_pos}",
                f"--stop-position={stop_pos}",
                f"/srv/labsdb/binlogs/{logfile}",
            ],
            cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
        )


class ToolsDBController:
    def __init__(self, remote: Remote, cluster_name: ToolforgeToolsDBClusterName):
        self.cluster_name = cluster_name
        self._remote = remote
        # cache the inventory
        self._inventory = get_inventory(load_dynamic_inventory=True, remote=self._remote)
        self.primary_node_fqdn = get_nodes_by_role(
            cluster_name=self.cluster_name,
            role_name=ToolforgeToolsDBNodeRoleName.PRIMARY,
            inventory=self._inventory,
        )[0]
        self.primary_node = MariaDBNode(
            node=self._remote.query(f"D{{{self.primary_node_fqdn}}}", use_sudo=True),
            fqdn=self.primary_node_fqdn,
            role=ToolforgeToolsDBNodeRoleName.PRIMARY,
        )

    def get_cluster_status(self) -> ClusterStatus:
        primary_status = self.primary_node.get_node_status()
        replica_nodes = self.get_replica_nodes()
        return ClusterStatus(
            primary=primary_status,
            replicas=[replica.get_node_status() for replica in replica_nodes.values()],
        )

    def get_replica_nodes(self) -> dict[str, MariaDBNode]:
        """Gets the replica nodes as defined in the inventory.

        To get the ones connected use `MariaDBNode.get_connected_replica_ids`.
        """
        defined_replicas = get_nodes_by_role(
            cluster_name=self.cluster_name,
            role_name=ToolforgeToolsDBNodeRoleName.REPLICA,
            inventory=self._inventory,
        )
        return {
            defined_replica: MariaDBNode(
                node=self._remote.query(f"D{{{defined_replica}}}", use_sudo=True),
                fqdn=defined_replica,
                role=ToolforgeToolsDBNodeRoleName.REPLICA,
            )
            for defined_replica in defined_replicas
        }
