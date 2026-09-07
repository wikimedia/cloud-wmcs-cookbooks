from wmcs_libs.etcd.clusters import (
    get_cluster_node_prefix,
    get_cluster_node_server_group_name,
    get_cluster_security_group_name,
)
from wmcs_libs.inventory.etcd import EtcdClusterName


def test_get_cluster_security_group_name() -> None:
    assert get_cluster_security_group_name(EtcdClusterName.TOOLS_K8S) == "tools-new-k8s-full-connectivity"
    assert get_cluster_security_group_name(EtcdClusterName.TOOLSBETA_K8S) == "toolsbeta-k8s-full-connectivity"


def test_get_cluster_node_prefix() -> None:
    assert get_cluster_node_prefix(EtcdClusterName.TOOLS_K8S) == "tools-k8s-etcd"
    assert get_cluster_node_prefix(EtcdClusterName.TOOLSBETA_K8S) == "toolsbeta-test-k8s-etcd"


def test_get_cluster_node_server_group_name() -> None:
    assert get_cluster_node_server_group_name(EtcdClusterName.TOOLS_K8S) == "tools-k8s-etcd"
    assert get_cluster_node_server_group_name(EtcdClusterName.TOOLSBETA_K8S) == "toolsbeta-k8s-etcd"
