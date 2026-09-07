from dataclasses import dataclass

from wmcs_libs.inventory.cluster import Cluster, ClusterType
from wmcs_libs.inventory.exceptions import InventoryError
from wmcs_libs.inventory.openstack import (
    OpenstackClusterName,
    OpenStackProjectSpecificClusterName,
)
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName


class EtcdClusterName(OpenStackProjectSpecificClusterName):
    """Every Etcd cluster we manage in Cloud VPS."""

    TOOLS_K8S = "tools-k8s"
    TOOLSBETA_K8S = "toolsbeta-k8s"

    def get_type(self) -> ClusterType:
        """Get the cluster type from the name"""
        return ClusterType.ETCD

    def get_openstack_cluster_name(self) -> OpenstackClusterName:
        """Get the OpenStack cluster/deployment where a cluster is deployed in by the name."""
        return OpenstackClusterName.EQIAD1

    def get_project(self) -> str:
        """Get the OpenStack cluster project where a cluster is deployed in by the name."""
        if self == EtcdClusterName.TOOLS_K8S:
            return "tools"
        if self == EtcdClusterName.TOOLSBETA_K8S:
            return "toolsbeta"

        raise InventoryError(f"I don't know which project the cluster {self} is in.")


@dataclass(frozen=True)
class EtcdCluster(Cluster):
    """Etcd cluster definition."""

    instance_prefix: str
    security_group_name: str
    server_group_name: str
    toolforge_k8s_cluster: ToolforgeKubernetesClusterName | None = None
