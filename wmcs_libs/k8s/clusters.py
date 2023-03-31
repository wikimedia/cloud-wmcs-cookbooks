"""Toolforge Kubernetes related classes and functions."""
from __future__ import annotations

import argparse
from functools import partial
from typing import Callable

from spicerack import Spicerack

from wmcs_libs.common import CommonOpts, add_common_opts
from wmcs_libs.inventory import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName, get_nodes_by_role


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
