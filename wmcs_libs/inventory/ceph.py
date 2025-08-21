from __future__ import annotations

from dataclasses import dataclass
from enum import auto

from wmcs_libs.inventory.cluster import Cluster, ClusterName, ClusterType, NodeRoleName, SiteName
from wmcs_libs.inventory.exceptions import InventoryError


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


@dataclass(frozen=True)
class CephCluster(Cluster):
    """Ceph cluster definition."""

    name: CephClusterName
    nodes_by_role: dict[CephNodeRoleName, list[str]]
    osd_drives_count: int
    expected_ceph_version: str
