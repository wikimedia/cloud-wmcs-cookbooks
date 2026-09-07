import argparse
from functools import partial
from typing import Callable, cast

from spicerack import Spicerack

from wmcs_libs.common import CommonOpts, add_common_opts
from wmcs_libs.inventory.cluster import ClusterType
from wmcs_libs.inventory.dynamic import get_inventory
from wmcs_libs.inventory.etcd import EtcdCluster, EtcdClusterName
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName


def add_etcd_cluster_opts(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Adds argparse arguments to work with Etcd Kubernetes clusters."""
    parser.add_argument(
        "--cluster-name",
        required=True,
        choices=list(EtcdClusterName),
        type=EtcdClusterName,
        help="cluster to work on",
    )

    return add_common_opts(parser, project_default=None)


def with_etcd_cluster_opts(spicerack: Spicerack, args: argparse.Namespace, runner: Callable) -> Callable:
    """Helper to add CommonOpts and cluster_name to a cookbook instantiation."""
    no_dologmsg = bool(spicerack.dry_run or args.no_dologmsg)
    cluster_name = args.cluster_name

    common_opts = CommonOpts(project=cluster_name.get_project(), task_id=args.task_id, no_dologmsg=no_dologmsg)

    return partial(runner, common_opts=common_opts, cluster_name=cluster_name)


def _get_cluster(cluster_name: EtcdClusterName) -> EtcdCluster:
    site = cluster_name.get_site()
    inventory = get_inventory()
    return cast(
        EtcdCluster,
        inventory[site].clusters_by_type[ClusterType.ETCD][cluster_name],
    )


def get_cluster_security_group_name(cluster_name: EtcdClusterName) -> str:
    """Gets the name of the OpenStack security group that is used between all the instances of a given cluster."""
    cluster = _get_cluster(cluster_name)
    return cluster.security_group_name


def get_cluster_node_prefix(cluster_name: EtcdClusterName) -> str:
    """Gets the naming prefix for nodes for instances in a given cluster."""
    cluster = _get_cluster(cluster_name)
    return cluster.instance_prefix


def get_cluster_node_server_group_name(cluster_name: EtcdClusterName) -> str:
    """Gets the name of the OpenStack server group to use for instances in a given cluster."""
    cluster = _get_cluster(cluster_name)
    return cluster.server_group_name


def get_cluster_hiera_member_lists(cluster_name: EtcdClusterName) -> list[str]:
    """Gets the Hiera keys for lists of instances in a given cluster."""
    cluster = _get_cluster(cluster_name)
    return cluster.hiera_member_lists


def get_cluster_related_toolforge_k8s_cluster(cluster_name: EtcdClusterName) -> ToolforgeKubernetesClusterName | None:
    """Gets the name of the Kubernetes cluster a given Etcd cluster is related to, if any."""
    cluster = _get_cluster(cluster_name)
    return cluster.toolforge_k8s_cluster
