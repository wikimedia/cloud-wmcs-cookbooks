"""Module that holds knowledge of what hosts exist in our deployments.

Static knowledge only, for dynamically loaded one see `.inventory.dynamic.get_inventory`.
Use to bootstrap the dynamically loaded inventory, or load it faster if you don't need the dynamic info.

See `.static.get_static_inventory` and `.dynamic.get_inventory`
"""

from __future__ import annotations

import logging

from wmcs_libs.inventory.ceph import CephCluster, CephClusterName, CephNodeRoleName
from wmcs_libs.inventory.cluster import ClusterType, Site, SiteName
from wmcs_libs.inventory.openstack import OpenstackCluster, OpenstackClusterName, OpenstackNodeRoleName
from wmcs_libs.inventory.toolsk8s import (
    ToolforgeKubernetesCluster,
    ToolforgeKubernetesClusterName,
    ToolforgeKubernetesNodeRoleName,
)

LOGGER = logging.getLogger(__name__)

Inventory = dict[SiteName, Site]

# TODO: replace this with different sources (dynamic or not) for hosts, ex. netbox, openstack cluster, ceph cluster,
#       k8s cluster ...
# structure is site -> cluster type (openstack, ceph, ...) -> cluster name -> node role -> node
# Use the get_inventory function to get this so it will be easy to generate in the future
# Use FQDNs here
_INVENTORY: Inventory = {
    SiteName.EQIAD: Site(
        name=SiteName.EQIAD,
        clusters_by_type={
            ClusterType.CEPH: {
                CephClusterName.EQIAD1: CephCluster(
                    name=CephClusterName.EQIAD1,
                    nodes_by_role={
                        CephNodeRoleName.MON: [
                            "cloudcephmon1004.eqiad.wmnet",
                            "cloudcephmon1005.eqiad.wmnet",
                            "cloudcephmon1006.eqiad.wmnet",
                        ]
                    },
                    osd_drives_count=8,
                )
            },
            ClusterType.OPENSTACK: {
                OpenstackClusterName.EQIAD1: OpenstackCluster(
                    name=OpenstackClusterName.EQIAD1,
                    nodes_by_role={
                        OpenstackNodeRoleName.CONTROL: [
                            "cloudcontrol1006.eqiad.wmnet",
                            "cloudcontrol1007.eqiad.wmnet",
                            "cloudcontrol1011.eqiad.wmnet",
                        ],
                        OpenstackNodeRoleName.GATEWAY: [
                            "cloudgw1001.eqiad.wmnet",
                            "cloudgw1002.eqiad.wmnet",
                        ],
                    },
                    internal_network_name="VXLAN/IPv6-dualstack",
                ),
            },
            ClusterType.TOOLFORGE_KUBERNETES: {
                ToolforgeKubernetesClusterName.TOOLS: ToolforgeKubernetesCluster(
                    name=ToolforgeKubernetesClusterName.TOOLS,
                    instance_prefix="tools",
                    security_group_name="tools-new-k8s-full-connectivity",
                    api_vip_fqdn="k8s.svc.tools.eqiad1.wikimedia.cloud",
                    nodes_by_role={
                        ToolforgeKubernetesNodeRoleName.CONTROL: [
                            "tools-k8s-control-7.tools.eqiad1.wikimedia.cloud",
                            "tools-k8s-control-8.tools.eqiad1.wikimedia.cloud",
                            "tools-k8s-control-9.tools.eqiad1.wikimedia.cloud",
                        ],
                        ToolforgeKubernetesNodeRoleName.SERVICES: [
                            "tools-services-06.tools.eqiad1.wikimedia.cloud",
                        ],
                        ToolforgeKubernetesNodeRoleName.BASTION: [
                            "tools-bastion-12.tools.eqiad1.wikimedia.cloud",
                            "tools-bastion-13.tools.eqiad1.wikimedia.cloud",
                            "tools-sgebastion-10.tools.eqiad1.wikimedia.cloud",
                        ],
                    },
                ),
                ToolforgeKubernetesClusterName.TOOLSBETA: ToolforgeKubernetesCluster(
                    name=ToolforgeKubernetesClusterName.TOOLSBETA,
                    instance_prefix="toolsbeta-test",
                    security_group_name="toolsbeta-k8s-full-connectivity",
                    api_vip_fqdn="k8s.svc.toolsbeta.eqiad1.wikimedia.cloud",
                    nodes_by_role={
                        ToolforgeKubernetesNodeRoleName.CONTROL: [
                            "toolsbeta-test-k8s-control-10.toolsbeta.eqiad1.wikimedia.cloud",
                            "toolsbeta-test-k8s-control-11.toolsbeta.eqiad1.wikimedia.cloud",
                            "toolsbeta-test-k8s-control-12.toolsbeta.eqiad1.wikimedia.cloud",
                        ],
                        ToolforgeKubernetesNodeRoleName.SERVICES: [
                            "tools-services-06.tools.eqiad1.wikimedia.cloud",
                        ],
                        ToolforgeKubernetesNodeRoleName.BASTION: [
                            "toolsbeta-bastion-6.toolsbeta.eqiad1.wikimedia.cloud",
                        ],
                    },
                ),
            },
        },
    ),
    SiteName.CODFW: Site(
        name=SiteName.CODFW,
        clusters_by_type={
            ClusterType.CEPH: {
                CephClusterName.CODFW1: CephCluster(
                    name=CephClusterName.CODFW1,
                    nodes_by_role={
                        CephNodeRoleName.MON: [
                            "cloudcephmon2004-dev.codfw.wmnet",
                            "cloudcephmon2005-dev.codfw.wmnet",
                            "cloudcephmon2006-dev.codfw.wmnet",
                        ]
                    },
                    osd_drives_count=2,
                )
            },
            ClusterType.OPENSTACK: {
                OpenstackClusterName.CODFW1DEV: OpenstackCluster(
                    name=OpenstackClusterName.CODFW1DEV,
                    nodes_by_role={
                        OpenstackNodeRoleName.CONTROL: [
                            "cloudcontrol2005-dev.codfw.wmnet",
                            "cloudcontrol2006-dev.codfw.wmnet",
                            "cloudcontrol2010-dev.codfw.wmnet",
                        ],
                        OpenstackNodeRoleName.GATEWAY: [
                            "cloudgw2001-dev.codfw.wmnet",
                            "cloudgw2002-dev.codfw.wmnet",
                            "cloudgw2003-dev.codfw.wmnet",
                        ],
                    },
                    internal_network_name="VXLAN/IPv6-dualstack",
                )
            },
        },
    ),
}


def get_static_inventory() -> Inventory:
    """Retrieve the known static inventory for WMCS infra.

    If you are using one of:
    * Openstack
    * Ceph
    * Toolforge kubernetes

    You can use this one, otherwise use `wmcs_lib.inventory.get_inventory`
    """
    return _INVENTORY
