from __future__ import annotations

from dataclasses import dataclass

from wmcs_libs.inventory.cluster import Cluster, ClusterName, ClusterType, NodeRoleName, SiteName
from wmcs_libs.inventory.exceptions import InventoryError


class OpenstackNodeRoleName(NodeRoleName):
    """Different types of openstack node roles."""

    GATEWAY = "cloudgw"
    CONTROL = "cloudcontrol"
    SERVICES = "cloudservices"
    NET = "cloudnet"
    VIRT = "cloudvirt"


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


@dataclass(frozen=True)
class OpenstackCluster(Cluster):
    """Openstack cluster definition."""

    name: OpenstackClusterName
    nodes_by_role: dict[OpenstackNodeRoleName, list[str]]
    internal_network_name: str
