from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from wmcs_libs.common import ArgparsableEnum


class ClusterType(Enum):
    """Different types of clusters we handle."""

    OPENSTACK = auto()
    CEPH = auto()
    TOOLFORGE_KUBERNETES = auto()
    TOOLFORGE_TOOLSDB = auto()


class SiteName(Enum):
    """Sites we have infrastructure in."""

    EQIAD = "eqiad"
    CODFW = "codfw"

    def __str__(self):
        """String representation"""
        return self.value


class ClusterName(ArgparsableEnum):
    """Base class for a cluster name."""

    def get_site(self) -> SiteName:
        """Get the site a cluster is deployed in by the name."""
        raise NotImplementedError()

    def get_type(self) -> ClusterType:
        """Get the cluster type from the name"""
        raise NotImplementedError()


class NodeRoleName(ArgparsableEnum):
    """Base node role name class, for inheritance."""


@dataclass(frozen=True)
class Cluster:
    """Base cluster, to be used as parent."""

    name: ClusterName
    # Enum as dict key does not match correctly to an Enum superclass (ex. CephNodeRoleName), so use Any
    nodes_by_role: dict[Any, list[str]]


@dataclass(frozen=True)
class Site:
    """A whole site representation, with support for multi-clusters."""

    name: SiteName
    clusters_by_type: dict[ClusterType, dict[Any, Cluster]]
