"""Toolforge Kubernetes related classes and functions."""

from __future__ import annotations

import argparse
from functools import partial
from typing import Callable, cast

from spicerack import Spicerack

from wmcs_libs.common import CommonOpts, add_common_opts
from wmcs_libs.inventory.cluster import ClusterType
from wmcs_libs.inventory.dynamic import get_inventory
from wmcs_libs.inventory.libs import get_nodes_by_role
from wmcs_libs.inventory.toolsk8s import (
    ToolforgeKubernetesCluster,
    ToolforgeKubernetesClusterName,
    ToolforgeKubernetesNodeRoleName,
)


def add_toolforge_kubernetes_cluster_opts(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Adds argparse arguments to work with Toolforge Kubernetes clusters."""
    parser.add_argument(
        "--cluster-name",
        required=True,
        choices=list(ToolforgeKubernetesClusterName),
        type=ToolforgeKubernetesClusterName,
        help="cluster to work on",
    )

    return add_common_opts(parser, project_default=None)


def with_toolforge_kubernetes_cluster_opts(
    spicerack: Spicerack, args: argparse.Namespace, runner: Callable
) -> Callable:
    """Helper to add CommonOpts and cluster_name to a cookbook instantiation."""
    no_dologmsg = bool(spicerack.dry_run or args.no_dologmsg)
    cluster_name = args.cluster_name

    common_opts = CommonOpts(project=cluster_name.get_project(), task_id=args.task_id, no_dologmsg=no_dologmsg)

    return partial(runner, common_opts=common_opts, cluster_name=cluster_name)


def get_control_nodes(cluster_name: ToolforgeKubernetesClusterName) -> list[str]:
    """Get the list of control nodes given a cluster."""
    return get_nodes_by_role(cluster_name, role_name=ToolforgeKubernetesNodeRoleName.CONTROL)


def _get_cluster(cluster_name: ToolforgeKubernetesClusterName) -> ToolforgeKubernetesCluster:
    site = cluster_name.get_site()
    inventory = get_inventory()
    return cast(
        ToolforgeKubernetesCluster, inventory[site].clusters_by_type[ClusterType.TOOLFORGE_KUBERNETES][cluster_name]
    )


def get_cluster_security_group_name(cluster_name: ToolforgeKubernetesClusterName) -> str:
    """Gets the name of the OpenStack security group that is used between all the members of a given cluster."""
    cluster = _get_cluster(cluster_name)
    return cluster.security_group_name


def get_cluster_node_prefix(cluster_name: ToolforgeKubernetesClusterName, role: ToolforgeKubernetesNodeRoleName) -> str:
    """Gets the naming prefix for nodes with a given role in a given cluster."""
    cluster = _get_cluster(cluster_name)
    return f"{cluster.instance_prefix}-k8s-{role.value}"


def get_cluster_node_server_group_name(
    cluster_name: ToolforgeKubernetesClusterName, role: ToolforgeKubernetesNodeRoleName
) -> str:
    """Gets the name of the OpenStack server group to use for given role in a given cluster."""
    return f"{cluster_name.name.lower()}-k8s-{role.value}"


def get_cluster_api_vip_fqdn(cluster_name: ToolforgeKubernetesClusterName) -> str:
    """Gets the FQDN of the service IP address used to access the Kubernetes API."""
    cluster = _get_cluster(cluster_name)
    return cluster.api_vip_fqdn
