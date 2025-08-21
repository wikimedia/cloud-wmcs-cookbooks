"""Main libs to interact with the inventory.

Note that this module should *not* import anything from the wmcs_libs.inventory.dynamic to avoid cyclic loops, all
functions should allow passing the inventory as a parameter instead.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Tuple, cast

from wmcs_libs.inventory.ceph import CephCluster, CephClusterName, CephNodeRoleName
from wmcs_libs.inventory.cluster import ClusterName, ClusterType, NodeRoleName, SiteName
from wmcs_libs.inventory.exceptions import InventoryError
from wmcs_libs.inventory.openstack import (
    OpenstackCluster,
    OpenstackClusterName,
    OpenstackNodeRoleName,
    OpenStackProjectSpecificClusterName,
)
from wmcs_libs.inventory.static import Inventory, get_static_inventory
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesNodeRoleName

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeInventoryInfo:
    """An info package with some node information with regards to the inventory."""

    site_name: SiteName
    openstack_project: str | None = None
    cluster_type: ClusterType | None = None
    cluster_name: ClusterName | None = None
    role_name: NodeRoleName | None = None


def _guess_node_site(node: str) -> SiteName | None:
    """Try to guess the site a node is from.

    * Check the hosts domain name (<site>.wmnet, <deployment>.wikimedia.cloud)
    * Check the host name (<name>YXXX.<domain>, where Y symbolizes the site)
    """
    if node.endswith(".wikimedia.cloud"):
        cluster_name = node.rsplit(".", 3)[1]
        for cluster in OpenstackClusterName:
            if cluster.value == cluster_name:
                return cluster.get_site()

    elif node.count(".") >= 2:
        domain = node.rsplit(".", 2)[1]
        for site_name in SiteName:
            if site_name.value.startswith(domain):
                return site_name

    deploy_match = re.match(r"[^.]*[^\d](?P<deployment_number>\d)\d+", node)
    if deploy_match:
        if deploy_match.groupdict()["deployment_number"] == "1":
            return SiteName.EQIAD
        if deploy_match.groupdict()["deployment_number"] == "2":
            return SiteName.CODFW

    return None


def _guess_cluster_type(node: str) -> ClusterType | None:
    if node.startswith("cloudceph"):
        return ClusterType.CEPH

    if (
        node.startswith("cloudcontrol")
        or node.startswith("cloudgw")
        or node.startswith("cloudvirt")
        or node.startswith("cloudnet")
        or node.startswith("cloudweb")
    ):
        return ClusterType.OPENSTACK

    if "-k8s-" in node:
        return ClusterType.TOOLFORGE_KUBERNETES

    if "-db-" in node and node.startswith("tools"):
        return ClusterType.TOOLFORGE_TOOLSDB

    return None


def _guess_openstack_project(node: str) -> str | None:
    if not node.endswith(".wikimedia.cloud"):
        return None
    return node.split(".")[1]


def _guess_cluster_name(
    site_name: SiteName,
    cluster_type: ClusterType | None,
    openstack_project: str | None,
    inventory: Inventory,
) -> ClusterName | None:
    if not cluster_type:
        return None

    if site_name not in inventory:
        raise InventoryError(f"Unknown site {site_name}, known sites: {inventory.keys()}")

    if cluster_type not in inventory[site_name].clusters_by_type:
        raise InventoryError(
            f"Unknown cluster type {cluster_type} for site {site_name}, known cluster types: "
            f"{inventory[site_name].clusters_by_type.keys()}"
        )

    clusters = inventory[site_name].clusters_by_type[cluster_type]

    if isinstance(next(iter(clusters.values())).name, OpenStackProjectSpecificClusterName):
        if not openstack_project:
            raise InventoryError(
                f"Unable to detect OpenStack project, but cluster type {cluster_type} is project specific"
            )

        for cluster in clusters:
            if cluster.get_project() == openstack_project:
                return cluster

        raise InventoryError(f"No clusters with type {cluster_type} found in project {openstack_project}")

    if len(clusters) == 1:
        return next(iter(clusters.values())).name

    raise InventoryError(f"More than one cluster of type {cluster_type} on site {site_name}: {clusters}")


def _guess_role_name(  # pylint: disable=too-many-return-statements
    node: str,
) -> OpenstackNodeRoleName | CephNodeRoleName | ToolforgeKubernetesNodeRoleName | None:
    if node.startswith("cloudcephosd"):
        return CephNodeRoleName.OSD
    if node.startswith("cloudcephmon"):
        return CephNodeRoleName.MON

    if node.startswith("cloudcontrol"):
        return OpenstackNodeRoleName.CONTROL
    if node.startswith("cloudservices"):
        return OpenstackNodeRoleName.SERVICES
    if node.startswith("cloudgw"):
        return OpenstackNodeRoleName.GATEWAY
    if node.startswith("cloudnet"):
        return OpenstackNodeRoleName.NET
    if node.startswith("cloudvirt"):
        return OpenstackNodeRoleName.VIRT

    if "-k8s-control-" in node:
        return ToolforgeKubernetesNodeRoleName.CONTROL

    return None


def get_node_inventory_info(node: str, inventory: Inventory | None = None) -> NodeInventoryInfo:
    """Retrieve the site given a node fqdn/name.

    This tries several strategies in priority order:
    * Check the known inventory
    * Check the hosts domain name (<site>.wmnet)
    * Check the host name (<name>YXXX.<domain>, where Y symbolizes the site)
    """
    if inventory is None:
        inventory = get_static_inventory()

    for site_name, site in inventory.items():
        for cluster_type, clusters in site.clusters_by_type.items():
            for cluster_name, cluster in clusters.items():
                for node_role_name, nodes in cluster.nodes_by_role.items():
                    if node in nodes:
                        return NodeInventoryInfo(
                            site_name=site_name,
                            cluster_type=cluster_type,
                            cluster_name=cluster_name,
                            role_name=node_role_name,
                            openstack_project=(
                                cluster_name.get_project()
                                if isinstance(cluster_name, OpenStackProjectSpecificClusterName)
                                else None
                            ),
                        )

    node_site = _guess_node_site(node=node)

    if not node_site:
        raise InventoryError(
            f"Unable to guess any inventory info for node {node}, please review the name passed and/or update the code "
            "to handle that node name."
        )

    guessed_cluster_type = _guess_cluster_type(node=node)
    guessed_openstack_project = _guess_openstack_project(node=node)
    return NodeInventoryInfo(
        site_name=node_site,
        openstack_project=guessed_openstack_project,
        cluster_type=guessed_cluster_type,
        cluster_name=_guess_cluster_name(
            site_name=node_site,
            cluster_type=guessed_cluster_type,
            openstack_project=guessed_openstack_project,
            inventory=inventory,
        ),
        role_name=_guess_role_name(node=node),
    )


def generic_get_node_cluster_name(node: str, inventory: Inventory | None = None) -> ClusterName:
    """Try to get the node cluster_name or raise.

    Prefer the specific wrapper for each service, as it has the specific return type,
    ex. `openstack.common.get_node_cluster_name`.
    """
    inventory_info = get_node_inventory_info(node=node, inventory=inventory)
    if not inventory_info.cluster_name:
        raise InventoryError(f"Unable to get cluster name for node {node}, got: {inventory_info}")

    return cast(ClusterName, inventory_info.cluster_name)


def get_nodes_by_role(cluster_name: ClusterName, role_name: Enum, inventory: Inventory | None = None) -> list[str]:
    """Retrieve the nodes of a given role for a given cluster."""
    if inventory is None:
        inventory = get_static_inventory()

    site = cluster_name.get_site()
    if site not in inventory:
        raise Exception(f"Unknown site {site} for cluster name {cluster_name}, known sites: {inventory.keys()}")

    cluster_type = cluster_name.get_type()
    if cluster_type not in inventory[site].clusters_by_type:
        raise Exception(
            f"Unknown cluster type {cluster_type} for site name {site}, known cluster types: "
            f"{inventory[site].clusters_by_type.keys()}"
        )

    if cluster_name not in inventory[site].clusters_by_type[cluster_type]:
        raise Exception(
            f"Unknown cluster name {cluster_name} for cluster {site}.{cluster_type}, known cluster names in "
            f"{site}.{cluster_type}: {inventory[site].clusters_by_type[cluster_type].keys()}"
        )

    nodes_by_role = inventory[site].clusters_by_type[cluster_type][cluster_name].nodes_by_role
    if role_name not in nodes_by_role:
        raise InventoryError(f"Unable to find any {role_name} nodes on cluster of name {cluster_name}.")

    return nodes_by_role[role_name]


def get_osd_drives_count(cluster_name: CephClusterName, inventory: Inventory | None = None) -> int:
    """Get the number of OSD drives for each host in a given Ceph cluster."""
    if inventory is None:
        inventory = get_static_inventory()

    site = cluster_name.get_site()
    cluster = cast(CephCluster, inventory[site].clusters_by_type[ClusterType.CEPH][cluster_name])

    return cluster.osd_drives_count


def get_expected_ceph_version(cluster_name: CephClusterName, inventory: Inventory | None = None) -> str:
    """Get the expected ceph package version for a given Ceph cluster."""
    if inventory is None:
        inventory = get_static_inventory()

    site = cluster_name.get_site()
    cluster = cast(CephCluster, inventory[site].clusters_by_type[ClusterType.CEPH][cluster_name])

    return cluster.expected_ceph_version


def get_openstack_internal_network_name(cluster_name: OpenstackClusterName, inventory: Inventory | None = None) -> str:
    """Get the openstack internal network name."""
    if inventory is None:
        inventory = get_static_inventory()

    site = cluster_name.get_site()
    os_cluster = cast(OpenstackCluster, inventory[site].clusters_by_type[ClusterType.OPENSTACK][cluster_name])

    return os_cluster.internal_network_name


def get_openstack_project_deployment(fqdn: str) -> Tuple[str, OpenstackClusterName]:
    """Guess the project and cluster of a Cloud VPS VM."""
    if not fqdn.endswith(".wikimedia.cloud"):
        raise InventoryError(f"'{fqdn}' does not seem to be a Cloud VPS VM")
    try:
        _, project, cluster_name, _, _ = fqdn.split(".")
        cluster = OpenstackClusterName(cluster_name)
    except ValueError as e:
        # A ValueError is thrown both when
        #  * there is a wrong number of segments in the FQDN to unpack
        #  * the cluster name is invalid
        raise InventoryError(f"Unable to parse FQDN '{fqdn}'") from e

    return project, cluster
