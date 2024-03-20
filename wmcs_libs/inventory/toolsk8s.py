from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from wmcs_libs.inventory.cluster import Cluster, ClusterType, NodeRoleName
from wmcs_libs.inventory.exceptions import InventoryError
from wmcs_libs.inventory.openstack import OpenstackClusterName, OpenStackProjectSpecificClusterName


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
    WORKER_NFS = "worker-nfs"
    INGRESS = "ingress"
    ETCD = "etcd"
    HAPROXY = "haproxy"

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
        return self not in (ToolforgeKubernetesNodeRoleName.ETCD, ToolforgeKubernetesNodeRoleName.HAPROXY)

    @property
    def is_worker(self) -> bool:
        """Check if this is a worker (including specialized worker roles)."""
        return self in (
            ToolforgeKubernetesNodeRoleName.WORKER,
            ToolforgeKubernetesNodeRoleName.WORKER_NFS,
            ToolforgeKubernetesNodeRoleName.INGRESS,
        )

    @property
    def has_extra_image_storage(self) -> bool:
        """Check if nodes in this role have an extra partition for container image storage."""
        return self in (ToolforgeKubernetesNodeRoleName.WORKER, ToolforgeKubernetesNodeRoleName.WORKER_NFS)

    @property
    def labels(self) -> set[str]:
        """A set of labels that must be applied to all new nodes of this role."""
        if self == ToolforgeKubernetesNodeRoleName.INGRESS:
            return {"kubernetes.io/role=ingressgen2"}
        return set()

    @property
    def taints(self) -> set[str]:
        """A set of taints that must be applied to all new nodes of this role."""
        if self == ToolforgeKubernetesNodeRoleName.INGRESS:
            return {"ingressgen2=true:NoSchedule"}
        return set()

    @property
    def list_in_hiera(self) -> Tuple["ToolforgeKubernetesNodeRoleName", str] | None:
        """A tuple of (role type, hiera key) that has a list of nodes of this type."""
        if self == ToolforgeKubernetesNodeRoleName.CONTROL:
            return (
                ToolforgeKubernetesNodeRoleName.HAPROXY,
                "profile::toolforge::k8s::control_nodes",
            )
        if self == ToolforgeKubernetesNodeRoleName.INGRESS:
            return (
                ToolforgeKubernetesNodeRoleName.HAPROXY,
                "profile::toolforge::k8s::ingress_nodes",
            )
        return None


@dataclass(frozen=True)
class ToolforgeKubernetesCluster(Cluster):
    """Toolforge Kubernetes cluster definition."""

    name: ToolforgeKubernetesClusterName
    instance_prefix: str
    security_group_name: str
    nodes_by_role: dict[ToolforgeKubernetesNodeRoleName, list[str]]
    api_vip_fqdn: str
