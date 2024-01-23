from __future__ import annotations

from dataclasses import dataclass

from wmcs_libs.inventory.cluster import Cluster, ClusterType, NodeRoleName
from wmcs_libs.inventory.openstack import OpenstackClusterName, OpenStackProjectSpecificClusterName


class ToolforgeToolsDBNodeRoleName(NodeRoleName):
    """Toolforge ToolsDB node roles."""

    PRIMARY = "primary"
    REPLICA = "replica"

    def __str__(self) -> str:
        """Needed to show the nice string values and for argparse to use those to call the `type` parameter."""
        return self.name.lower()

    @classmethod
    def from_str(cls, arg: str) -> "ToolforgeToolsDBNodeRoleName":
        """Helps when passing ToolforgeToolsDBNodeRoleName to argparse as type."""
        return cls[arg.upper()]


class ToolforgeToolsDBClusterName(OpenStackProjectSpecificClusterName):
    """Names of toolsdb clusters we have."""

    TOOLS = "tools"

    def get_type(self) -> ClusterType:
        """Get the cluster type from the name"""
        return ClusterType.TOOLFORGE_TOOLSDB

    def get_openstack_cluster_name(self) -> OpenstackClusterName:
        """Get the OpenStack cluster/deployment where a cluster is deployed in by the name."""
        return OpenstackClusterName.EQIAD1

    def get_project(self) -> str:
        """Get the OpenStack cluster project where a cluster is deployed in by the name."""
        return "tools"


@dataclass(frozen=True)
class ToolforgeToolsDBCluster(Cluster):
    """Toolforge ToolsDB cluster definition."""

    name: ToolforgeToolsDBClusterName
    instance_prefix: str
    security_group_name: str
    nodes_by_role: dict[ToolforgeToolsDBNodeRoleName, list[str]]
