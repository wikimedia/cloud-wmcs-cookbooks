from wmcs_libs.inventory.cluster import ClusterType
from wmcs_libs.inventory.openstack import OpenstackCluster
from wmcs_libs.inventory.static import get_static_inventory


def get_openstack_clusters() -> list[OpenstackCluster]:
    ret = []
    inventory = get_static_inventory()

    for item in inventory.items():
        # tuple(SiteName, Site)
        site = item[1]
        for cluster in site.clusters_by_type.get(ClusterType.OPENSTACK, []):
            ret.append(cluster)

    return ret
