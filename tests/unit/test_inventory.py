from typing import Type

import pytest

from wmcs_libs.common import UtilsForTesting
from wmcs_libs.inventory.ceph import CephNodeRoleName
from wmcs_libs.inventory.cluster import Cluster, ClusterName, ClusterType, NodeRoleName, Site, SiteName
from wmcs_libs.inventory.exceptions import InventoryError
from wmcs_libs.inventory.libs import NodeInventoryInfo, get_node_inventory_info, get_openstack_project_deployment
from wmcs_libs.inventory.openstack import OpenstackCluster, OpenstackClusterName, OpenstackNodeRoleName
from wmcs_libs.inventory.toolsdb import (
    ToolforgeToolsDBCluster,
    ToolforgeToolsDBClusterName,
    ToolforgeToolsDBNodeRoleName,
)
from wmcs_libs.inventory.toolsk8s import (
    ToolforgeKubernetesCluster,
    ToolforgeKubernetesClusterName,
    ToolforgeKubernetesNodeRoleName,
)


def get_dummy_inventory(
    node_fqdn: str = "dummy.no.de",
    site_name: SiteName = SiteName.CODFW,
    cluster_type: ClusterType = ClusterType.OPENSTACK,
    cluster_name: ClusterName = OpenstackClusterName.CODFW1DEV,
    cluster_class: Type[Cluster] = OpenstackCluster,
    role_name: NodeRoleName = OpenstackNodeRoleName.CONTROL,
    cluster_extra_args: dict = {"internal_network_name": "VXLAN/IPv6-dualstack"},
) -> dict[SiteName, Site]:
    return {
        site_name: Site(
            name=site_name,
            clusters_by_type={
                cluster_type: {
                    cluster_name: cluster_class(
                        name=cluster_name,
                        nodes_by_role={role_name: [node_fqdn]},
                        **cluster_extra_args,
                    )
                }
            },
        )
    }


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "Node in inventory matches inventory site": {
                "node_fqdn": "something.some.where",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.CODFW,
                    cluster_type=ClusterType.OPENSTACK,
                    cluster_name=OpenstackClusterName.CODFW1DEV,
                    role_name=OpenstackNodeRoleName.CONTROL,
                ),
                "inventory": get_dummy_inventory(
                    node_fqdn="something.some.where",
                    site_name=SiteName.CODFW,
                    cluster_type=ClusterType.OPENSTACK,
                    cluster_name=OpenstackClusterName.CODFW1DEV,
                    role_name=OpenstackNodeRoleName.CONTROL,
                ),
            },
            "Node in inventory matches inventory site, even if wikimedia.org domain": {
                "node_fqdn": "something.wikimedia.org",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.CODFW,
                    cluster_type=ClusterType.OPENSTACK,
                    cluster_name=OpenstackClusterName.CODFW1DEV,
                    role_name=OpenstackNodeRoleName.CONTROL,
                ),
                "inventory": get_dummy_inventory(
                    node_fqdn="something.wikimedia.org",
                    site_name=SiteName.CODFW,
                    cluster_type=ClusterType.OPENSTACK,
                    cluster_name=OpenstackClusterName.CODFW1DEV,
                    role_name=OpenstackNodeRoleName.CONTROL,
                ),
            },
            "Node in inventory matches inventory site, even if wrong numeration": {
                "node_fqdn": "something1001.wikimedia.org",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.CODFW,
                    cluster_type=ClusterType.OPENSTACK,
                    cluster_name=OpenstackClusterName.CODFW1DEV,
                    role_name=OpenstackNodeRoleName.CONTROL,
                ),
                "inventory": get_dummy_inventory(
                    node_fqdn="something1001.wikimedia.org",
                    site_name=SiteName.CODFW,
                    cluster_type=ClusterType.OPENSTACK,
                    cluster_name=OpenstackClusterName.CODFW1DEV,
                    role_name=OpenstackNodeRoleName.CONTROL,
                ),
            },
            "Node not in inventory, with eqiad.wmnet matches eqiad site": {
                "node_fqdn": "something.eqiad.wmnet",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.EQIAD,
                ),
                "inventory": get_dummy_inventory(),
            },
            "Node not in inventory, with unknown domain, matches by number 2001 -> codfw": {
                "node_fqdn": "something2001.some.where",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.CODFW,
                ),
                "inventory": get_dummy_inventory(),
            },
            "Node not in inventory, with unknown domain, matches by number 1001 -> eqiad": {
                "node_fqdn": "something1001.some.where",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.EQIAD,
                ),
                "inventory": get_dummy_inventory(),
            },
            (
                "Node not in inventory, with correct domain, and cloudcephosd name matches correct role_name and "
                "cluster_type"
            ): {
                "node_fqdn": "cloudcephosd2001.codfw.wmnet",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.CODFW,
                    cluster_type=ClusterType.CEPH,
                    cluster_name=OpenstackClusterName.CODFW1DEV,
                    role_name=CephNodeRoleName.OSD,
                ),
                "inventory": get_dummy_inventory(
                    node_fqdn="some_other.host",
                    site_name=SiteName.CODFW,
                    cluster_type=ClusterType.CEPH,
                    cluster_name=OpenstackClusterName.CODFW1DEV,
                    role_name=CephNodeRoleName.OSD,
                ),
            },
            "Node not in inventory, matches OpenStack deployment host name and k8s role": {
                "node_fqdn": "toolsbeta-test-k8s-control-1.toolsbeta.eqiad1.wikimedia.cloud",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.EQIAD,
                    openstack_project="toolsbeta",
                    cluster_type=ClusterType.TOOLFORGE_KUBERNETES,
                    cluster_name=ToolforgeKubernetesClusterName.TOOLSBETA,
                    role_name=ToolforgeKubernetesNodeRoleName.CONTROL,
                ),
                "inventory": get_dummy_inventory(
                    site_name=SiteName.EQIAD,
                    cluster_type=ClusterType.TOOLFORGE_KUBERNETES,
                    cluster_name=ToolforgeKubernetesClusterName.TOOLSBETA,
                    role_name=ToolforgeKubernetesNodeRoleName.CONTROL,
                    cluster_class=ToolforgeKubernetesCluster,
                    cluster_extra_args={
                        "instance_prefix": "toolsbeta-test",
                        "security_group_name": "toolsbeta-k8s-full-connectivity",
                        "api_vip_fqdn": "k8s.svc.toolsbeta.eqiad1.wikimedia.cloud",
                        "prometheus_url": "https://dummy.local",
                    },
                ),
            },
            "Toolforge Kubernetes node in inventory": {
                "node_fqdn": "tools-k8s-control-1.tools.eqiad1.wikimedia.cloud",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.EQIAD,
                    openstack_project="tools",
                    cluster_type=ClusterType.TOOLFORGE_KUBERNETES,
                    cluster_name=ToolforgeKubernetesClusterName.TOOLS,
                    role_name=ToolforgeKubernetesNodeRoleName.CONTROL,
                ),
                "inventory": get_dummy_inventory(
                    node_fqdn="tools-k8s-control-1.tools.eqiad1.wikimedia.cloud",
                    site_name=SiteName.EQIAD,
                    cluster_type=ClusterType.TOOLFORGE_KUBERNETES,
                    cluster_name=ToolforgeKubernetesClusterName.TOOLS,
                    role_name=ToolforgeKubernetesNodeRoleName.CONTROL,
                    cluster_class=ToolforgeKubernetesCluster,
                    cluster_extra_args={
                        "instance_prefix": "tools",
                        "security_group_name": "tools-new-k8s-full-connectivity",
                        "api_vip_fqdn": "k8s.svc.toolsbeta.eqiad1.wikimedia.cloud",
                        "prometheus_url": "https://dummy.local",
                    },
                ),
            },
            "Toolforge ToolsDB node in inventory": {
                "node_fqdn": "tools-db-1.tools.eqiad1.wikimedia.cloud",
                "expected_node_inventory_info": NodeInventoryInfo(
                    site_name=SiteName.EQIAD,
                    openstack_project="tools",
                    cluster_type=ClusterType.TOOLFORGE_TOOLSDB,
                    cluster_name=ToolforgeToolsDBClusterName.TOOLS,
                    role_name=ToolforgeToolsDBNodeRoleName.PRIMARY,
                ),
                "inventory": get_dummy_inventory(
                    node_fqdn="tools-db-1.tools.eqiad1.wikimedia.cloud",
                    site_name=SiteName.EQIAD,
                    cluster_type=ClusterType.TOOLFORGE_TOOLSDB,
                    cluster_name=ToolforgeToolsDBClusterName.TOOLS,
                    role_name=ToolforgeToolsDBNodeRoleName.PRIMARY,
                    cluster_class=ToolforgeToolsDBCluster,
                    cluster_extra_args={
                        "instance_prefix": "tools-db",
                        "security_group_name": "toolsdb",
                    },
                ),
            },
        }
    )
)
def test_get_node_inventory_info(
    node_fqdn: str, expected_node_inventory_info: NodeInventoryInfo, inventory: dict[SiteName, Site]
):
    gotten_inventory_info = get_node_inventory_info(node=node_fqdn, inventory=inventory)

    assert gotten_inventory_info == expected_node_inventory_info


@pytest.mark.parametrize(
    "node_fqdn, expected_project, expected_cluster",
    [
        ("tools-k8s-control-N.tools.eqiad1.wikimedia.cloud", "tools", OpenstackClusterName.EQIAD1),
        (
            "toolsbeta-test-k8s-control-N.toolsbeta.codfw1dev.wikimedia.cloud",
            "toolsbeta",
            OpenstackClusterName.CODFW1DEV,
        ),
    ],
)
def test_get_openstack_project_deployment_ok(
    node_fqdn: str, expected_project: str, expected_cluster: OpenstackClusterName
) -> None:
    assert get_openstack_project_deployment(node_fqdn) == (expected_project, expected_cluster)


@pytest.mark.parametrize(
    "node_fqdn",
    [
        "foobar",
        "cloudcontrol1011.eqiad.wmnet",
        "tools.eqiad1.wikimedia.cloud",
        "foo.bar.baz.tools.eqiad1.wikimedia.cloud",
        "somevm.tools.invalid.wikimedia.cloud",
    ],
)
def test_get_openstack_project_deployment_invalid(node_fqdn: str) -> None:
    with pytest.raises(InventoryError):
        assert get_openstack_project_deployment(node_fqdn) is None
