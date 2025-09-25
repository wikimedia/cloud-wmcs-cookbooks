from wmcs_libs.inventory.toolsk8s import (
    ToolforgeKubernetesClusterName,
    ToolforgeKubernetesNodeRoleName,
)
from wmcs_libs.k8s.clusters import (
    get_cluster_node_server_group_name,
    get_cluster_security_group_name,
)


def test_get_cluster_security_group_name() -> None:
    # Usual case
    assert (
        get_cluster_security_group_name(
            ToolforgeKubernetesClusterName.TOOLS,
            ToolforgeKubernetesNodeRoleName.INGRESS,
        )
        == "tools-new-k8s-full-connectivity"
    )
    assert (
        get_cluster_security_group_name(
            ToolforgeKubernetesClusterName.TOOLSBETA,
            ToolforgeKubernetesNodeRoleName.WORKER_NFS,
        )
        == "toolsbeta-k8s-full-connectivity"
    )

    # HAProxies
    assert (
        get_cluster_security_group_name(
            ToolforgeKubernetesClusterName.TOOLS,
            ToolforgeKubernetesNodeRoleName.HAPROXY,
        )
        == "k8s-haproxy"
    )
    assert (
        get_cluster_security_group_name(
            ToolforgeKubernetesClusterName.TOOLSBETA,
            ToolforgeKubernetesNodeRoleName.HAPROXY,
        )
        == "test-k8s-haproxy"
    )


def test_get_cluster_node_server_group_name() -> None:
    # Old, Spicerack-style server group names
    assert (
        get_cluster_node_server_group_name(
            ToolforgeKubernetesClusterName.TOOLS,
            ToolforgeKubernetesNodeRoleName.INGRESS,
        )
        == "tools-k8s-ingress"
    )
    assert (
        get_cluster_node_server_group_name(
            ToolforgeKubernetesClusterName.TOOLSBETA,
            ToolforgeKubernetesNodeRoleName.WORKER_NFS,
        )
        == "toolsbeta-k8s-worker-nfs"
    )

    # New, Tofu-style server group names
    assert (
        get_cluster_node_server_group_name(
            ToolforgeKubernetesClusterName.TOOLS,
            ToolforgeKubernetesNodeRoleName.HAPROXY,
        )
        == "tools-k8s-haproxy"
    )
    assert (
        get_cluster_node_server_group_name(
            ToolforgeKubernetesClusterName.TOOLSBETA,
            ToolforgeKubernetesNodeRoleName.HAPROXY,
        )
        == "toolsbeta-test-k8s-haproxy"
    )
