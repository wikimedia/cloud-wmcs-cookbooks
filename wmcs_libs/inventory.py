"""Module that holds knowledge of what hosts exist in our deployments."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, cast

from wmcs_libs.common import ArgparsableEnum


class InventoryError(Exception):
    """Parent exception for the module."""


class SiteName(Enum):
    """Sites we have infrastructure in."""

    EQIAD = "eqiad"
    CODFW = "codfw"

    def __str__(self):
        """String representation"""
        return self.value


class ClusterType(Enum):
    """Different types of clusters we handle."""

    OPENSTACK = auto()
    CEPH = auto()
    TOOLFORGE_KUBERNETES = auto()


class ClusterName(ArgparsableEnum):
    """Base class for a cluster name."""

    def get_site(self) -> SiteName:
        """Get the site a cluster is deployed in by the name."""
        raise NotImplementedError()

    def get_type(self) -> ClusterType:
        """Get the cluster type from the name"""
        raise NotImplementedError()


class OpenStackProjectSpecificClusterName(ClusterName):
    """A cluster name which is specific to an OpenStack project."""

    def get_openstack_cluster_name(self) -> "OpenstackClusterName":
        """Get the OpenStack cluster/deployment where a cluster is deployed in by the name."""
        raise NotImplementedError()

    def get_site(self) -> SiteName:
        """Get the site a cluster is deployed in by the name."""
        return self.get_openstack_cluster_name().get_site()

    def get_project(self) -> str:
        """Get the OpenStack cluster project where a cluster is deployed in by the name."""
        raise NotImplementedError()


class OpenstackClusterName(ClusterName):
    """Every openstack cluster name we have (should be the same as deployment)."""

    EQIAD1 = "eqiad1"
    CODFW1DEV = "codfw1dev"

    def get_site(self) -> SiteName:
        """Get the site a cluster is deployed in by the name."""
        if self == OpenstackClusterName.EQIAD1:
            return SiteName.EQIAD
        if self == OpenstackClusterName.CODFW1DEV:
            return SiteName.CODFW

        raise InventoryError(f"I don't know which site the cluster {self} is in.")

    def get_type(self) -> ClusterType:
        """Get the cluster type from the name"""
        return ClusterType.OPENSTACK


class NodeRoleName(Enum):
    """Base node role name class, for inheritance."""


class OpenstackNodeRoleName(NodeRoleName):
    """Different types of openstack node roles."""

    GATEWAY = "cloudgw"
    CONTROL = "cloudcontrol"
    NET = "cloudnet"


class CephClusterName(ClusterName):
    """Names of ceph clusters we have."""

    EQIAD1 = "eqiad1"
    CODFW1 = "codfw1"

    def get_site(self) -> SiteName:
        """Get the site a cluster is deployed in by the name."""
        if self == CephClusterName.EQIAD1:
            return SiteName.EQIAD
        if self == CephClusterName.CODFW1:
            return SiteName.CODFW

        raise InventoryError(f"I don't know which site the cluster {self} is in.")

    def get_type(self) -> ClusterType:
        """Get the cluster type from the name"""
        return ClusterType.CEPH


class CephNodeRoleName(NodeRoleName):
    """Ceph node (not daemon) roles."""

    OSD = auto()
    MON = auto()


class ToolforgeKubernetesClusterName(OpenStackProjectSpecificClusterName):
    """Every Toolforge-like Kubernetes cluster we have."""

    TOOLS = "tools"
    TOOLSBETA = "toolsbeta"

    def get_type(self) -> ClusterType:
        """Get the cluster type from the name"""
        return ClusterType.TOOLFORGE_KUBERNETES

    def get_openstack_cluster_name(self) -> OpenstackClusterName:
        """Get the OpenStack cluster/deployment where a cluster is deployed in by the name."""
        return OpenstackClusterName.EQIAD1

    def get_project(self) -> str:
        """Get the OpenStack cluster project where a cluster is deployed in by the name."""
        if self == ToolforgeKubernetesClusterName.TOOLS:
            return "tools"
        if self == ToolforgeKubernetesClusterName.TOOLSBETA:
            return "toolsbeta"

        raise InventoryError(f"I don't know which project the cluster {self} is in.")


class ToolforgeKubernetesNodeRoleName(NodeRoleName):
    """Toolforge Kubernetes node roles."""

    CONTROL = "control"
    WORKER = "worker"
    INGRESS = "ingress"
    ETCD = "etcd"

    def __str__(self) -> str:
        """Needed to show the nice string values and for argparse to use those to call the `type` parameter."""
        return self.name.lower()

    @classmethod
    def from_str(cls, arg: str) -> "ToolforgeKubernetesNodeRoleName":
        """Helps when passing ToolforgeKubernetesNodeRoleName to argparse as type."""
        return cls[arg.upper()]

    @property
    def runs_kubelet(self) -> bool:
        """Check if this node type is a Kubernetes worker or control node."""
        return self != ToolforgeKubernetesNodeRoleName.ETCD

    @property
    def is_worker(self) -> bool:
        """Check if this is a worker (including specialized worker roles)."""
        return self in (ToolforgeKubernetesNodeRoleName.WORKER, ToolforgeKubernetesNodeRoleName.INGRESS)

    @property
    def has_extra_image_storage(self) -> bool:
        """Check if nodes in this role have an extra partition for container image storage."""
        return self == ToolforgeKubernetesNodeRoleName.WORKER


@dataclass(frozen=True)
class Cluster:
    """Base cluster, to be used as parent."""

    name: ClusterName
    # Enum as dict key does not match correctly to an Enum superclass (ex. CephNodeRoleName), so use Any
    nodes_by_role: dict[Any, list[str]]


@dataclass(frozen=True)
class CephCluster(Cluster):
    """Ceph cluster definition."""

    name: CephClusterName
    nodes_by_role: dict[CephNodeRoleName, list[str]]
    osd_drives_count: int


@dataclass(frozen=True)
class OpenstackCluster(Cluster):
    """Openstack cluster definition."""

    name: OpenstackClusterName
    nodes_by_role: dict[OpenstackNodeRoleName, list[str]]
    internal_network_name: str


@dataclass(frozen=True)
class ToolforgeKubernetesCluster(Cluster):
    """Toolforge Kubernetes cluster definition."""

    name: ToolforgeKubernetesClusterName
    instance_prefix: str
    security_group_name: str
    nodes_by_role: dict[ToolforgeKubernetesNodeRoleName, list[str]]


@dataclass(frozen=True)
class Site:
    """A whole site representation, with support for multi-clusters."""

    name: SiteName
    clusters_by_type: dict[ClusterType, dict[Any, Cluster]]


# TODO: replace this with different sources (dynamic or not) for hosts, ex. netbox, openstack cluster, ceph cluster,
#       k8s cluster ...
# structure is site -> cluster type (openstack, ceph, ...) -> cluster name -> node role -> node
# Use the get_inventory function to get this so it will be easy to generate in the future
# Use FQDNs here
_INVENTORY = {
    SiteName.EQIAD: Site(
        name=SiteName.EQIAD,
        clusters_by_type={
            ClusterType.CEPH: {
                CephClusterName.EQIAD1: CephCluster(
                    name=CephClusterName.EQIAD1,
                    nodes_by_role={
                        CephNodeRoleName.MON: [
                            "cloudcephmon1001.eqiad.wmnet",
                            "cloudcephmon1002.eqiad.wmnet",
                            "cloudcephmon1003.eqiad.wmnet",
                        ]
                    },
                    osd_drives_count=8,
                )
            },
            ClusterType.OPENSTACK: {
                OpenstackClusterName.EQIAD1: OpenstackCluster(
                    name=OpenstackClusterName.EQIAD1,
                    nodes_by_role={
                        OpenstackNodeRoleName.CONTROL: [
                            "cloudcontrol1005.wikimedia.org",
                            "cloudcontrol1006.wikimedia.org",
                            "cloudcontrol1007.wikimedia.org",
                        ],
                        OpenstackNodeRoleName.GATEWAY: [
                            "cloudgw1001.eqiad.wmnet",
                            "cloudgw1002.eqiad.wmnet",
                        ],
                    },
                    internal_network_name="lan-flat-cloudinstances2b",
                ),
            },
            ClusterType.TOOLFORGE_KUBERNETES: {
                ToolforgeKubernetesClusterName.TOOLS: ToolforgeKubernetesCluster(
                    name=ToolforgeKubernetesClusterName.TOOLS,
                    instance_prefix="tools",
                    security_group_name="tools-new-k8s-full-connectivity",
                    nodes_by_role={
                        ToolforgeKubernetesNodeRoleName.CONTROL: [
                            "tools-k8s-control-4.tools.eqiad1.wikimedia.cloud",
                            "tools-k8s-control-5.tools.eqiad1.wikimedia.cloud",
                            "tools-k8s-control-6.tools.eqiad1.wikimedia.cloud",
                        ],
                    },
                ),
                ToolforgeKubernetesClusterName.TOOLSBETA: ToolforgeKubernetesCluster(
                    name=ToolforgeKubernetesClusterName.TOOLSBETA,
                    instance_prefix="toolsbeta-test",
                    security_group_name="toolsbeta-k8s-full-connectivity",
                    nodes_by_role={
                        ToolforgeKubernetesNodeRoleName.CONTROL: [
                            "toolsbeta-test-k8s-control-4.toolsbeta.eqiad1.wikimedia.cloud",
                            "toolsbeta-test-k8s-control-5.toolsbeta.eqiad1.wikimedia.cloud",
                            "toolsbeta-test-k8s-control-6.toolsbeta.eqiad1.wikimedia.cloud",
                        ],
                    },
                ),
            },
        },
    ),
    SiteName.CODFW: Site(
        name=SiteName.CODFW,
        clusters_by_type={
            ClusterType.CEPH: {
                CephClusterName.CODFW1: CephCluster(
                    name=CephClusterName.CODFW1,
                    nodes_by_role={
                        CephNodeRoleName.MON: [
                            "cloudcephmon2004-dev.codfw.wmnet",
                            "cloudcephmon2005-dev.codfw.wmnet",
                            "cloudcephmon2006-dev.codfw.wmnet",
                        ]
                    },
                    osd_drives_count=2,
                )
            },
            ClusterType.OPENSTACK: {
                OpenstackClusterName.CODFW1DEV: OpenstackCluster(
                    name=OpenstackClusterName.CODFW1DEV,
                    nodes_by_role={
                        OpenstackNodeRoleName.CONTROL: [
                            "cloudcontrol2001-dev.wikimedia.org",
                            "cloudcontrol2003-dev.wikimedia.org",
                            "cloudcontrol2004-dev.wikimedia.org",
                        ],
                        OpenstackNodeRoleName.GATEWAY: [
                            "cloudgw2001-dev.codfw.wmnet",
                            "cloudgw2002-dev.codfw.wmnet",
                            "cloudgw2003-dev.codfw.wmnet",
                        ],
                    },
                    internal_network_name="lan-flat-cloudinstances2b",
                )
            },
        },
    ),
}


def get_inventory() -> dict[SiteName, Site]:
    """Retrieve the known inventory for WMCS infra."""
    return _INVENTORY


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

    return None


def _guess_openstack_project(node: str) -> str | None:
    if not node.endswith(".wikimedia.cloud"):
        return None
    return node.split(".")[1]


def _guess_cluster_name(
    site_name: SiteName, cluster_type: ClusterType | None, openstack_project: str | None
) -> ClusterName | None:
    if not cluster_type:
        return None

    inventory = get_inventory()
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


def _guess_role_name(node: str) -> OpenstackNodeRoleName | CephNodeRoleName | ToolforgeKubernetesNodeRoleName | None:
    if node.startswith("cloudcephosd"):
        return CephNodeRoleName.OSD
    if node.startswith("cloudcephmon"):
        return CephNodeRoleName.MON

    if node.startswith("cloudcontrol"):
        return OpenstackNodeRoleName.CONTROL
    if node.startswith("cloudgw"):
        return OpenstackNodeRoleName.GATEWAY

    if "-k8s-control-" in node:
        return ToolforgeKubernetesNodeRoleName.CONTROL

    return None


def get_node_inventory_info(node: str) -> NodeInventoryInfo:
    """Retrieve the site given a node fqdn/name.

    This tries several strategies in priority order:
    * Check the known inventory
    * Check the hosts domain name (<site>.wmnet)
    * Check the host name (<name>YXXX.<domain>, where Y symbolizes the site)
    """
    inventory = get_inventory()
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
    guessed_cluster_name = _guess_cluster_name(
        site_name=node_site, cluster_type=guessed_cluster_type, openstack_project=guessed_openstack_project
    )
    guessed_role_name = _guess_role_name(node=node)
    return NodeInventoryInfo(
        site_name=node_site,
        openstack_project=guessed_openstack_project,
        cluster_type=guessed_cluster_type,
        cluster_name=guessed_cluster_name,
        role_name=guessed_role_name,
    )


def generic_get_node_cluster_name(node: str) -> ClusterName:
    """Try to get the node cluster_name or raise.

    Prefer the specific wrapper for each service, as it has the specific return type,
    ex. `openstack.common.get_node_cluster_name`.
    """
    inventory_info = get_node_inventory_info(node=node)
    if not inventory_info.cluster_name:
        raise InventoryError(f"Unable to get cluster name for node {node}, got: {inventory_info}")

    return cast(ClusterName, inventory_info.cluster_name)


def get_nodes_by_role(cluster_name: ClusterName, role_name: Enum) -> list[str]:
    """Retrieve the nodes of a given role for a given cluster."""
    site = cluster_name.get_site()
    inventory = get_inventory()
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


def get_osd_drives_count(cluster_name: CephClusterName) -> int:
    """Get the number of OSD drives for each host in a given Ceph cluster."""
    site = cluster_name.get_site()
    inventory = get_inventory()
    cluster = cast(CephCluster, inventory[site].clusters_by_type[ClusterType.CEPH][cluster_name])

    return cluster.osd_drives_count


def get_openstack_internal_network_name(cluster_name: OpenstackClusterName) -> str:
    """Get the openstack internal network name."""
    site = cluster_name.get_site()
    inventory = get_inventory()
    os_cluster = cast(OpenstackCluster, inventory[site].clusters_by_type[ClusterType.OPENSTACK][cluster_name])

    return os_cluster.internal_network_name
