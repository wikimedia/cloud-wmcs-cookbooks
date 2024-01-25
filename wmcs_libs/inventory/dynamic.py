"""Dynamic inventory code."""

from __future__ import annotations

import logging

from spicerack.remote import Remote

from wmcs_libs.common import CUMIN_SAFE_WITHOUT_OUTPUT
from wmcs_libs.inventory.cluster import ClusterType, Site, SiteName
from wmcs_libs.inventory.libs import get_static_inventory
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.inventory.toolsdb import (
    ToolforgeToolsDBCluster,
    ToolforgeToolsDBClusterName,
    ToolforgeToolsDBNodeRoleName,
)
from wmcs_libs.openstack.common import OpenstackAPI
from wmcs_libs.openstack.enc import Enc

LOGGER = logging.getLogger(__name__)


def get_inventory(load_dynamic_inventory: bool = False, remote: Remote | None = None) -> dict[SiteName, Site]:
    """Retrieve the known inventory for WMCS infra.

    It will not load any dynamic information unless requested as it's an expensive task, that currently is:
    * ToolsDB information
    """
    inventory = get_static_inventory()
    if load_dynamic_inventory:
        if remote is None:
            raise Exception("To load the inventory dynamically you need to pass the remote also.")
        toolsdb_nodes_by_role = _get_toolsbd_nodes_by_role(remote=remote)
        inventory[SiteName.EQIAD].clusters_by_type[ClusterType.TOOLFORGE_TOOLSDB] = {
            ToolforgeToolsDBClusterName.TOOLS: ToolforgeToolsDBCluster(
                name=ToolforgeToolsDBClusterName.TOOLS,
                instance_prefix="tools-db",
                security_group_name="toolsdb",
                nodes_by_role=toolsdb_nodes_by_role,
            ),
        }
    return inventory


def _get_toolsbd_nodes_by_role(remote: Remote) -> dict[ToolforgeToolsDBNodeRoleName, list[str]]:
    server_prefix = "tools-db-"
    project = "tools"
    cluster_name = OpenstackClusterName.EQIAD1

    myenc = Enc(remote=remote, cluster_name=cluster_name)
    prefix_hiera = myenc.prefix(project_id=project, prefix_name=server_prefix).get_current_hiera()
    primary_server = prefix_hiera.get("profile::wmcs::services::toolsdb::primary_server", None)
    if primary_server is None:
        LOGGER.warning("No primary server found for toolsdb")
        return {}

    domain = ".tools.eqiad1.wikimedia.cloud"
    openstack_api = OpenstackAPI(cluster_name=cluster_name, remote=remote, project=project)
    secondary_servers = [
        server["Name"] + domain
        for server in openstack_api.server_list(cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)
        if server["Name"].startswith(server_prefix) and (server["Name"] + domain) != primary_server
    ]

    return {
        ToolforgeToolsDBNodeRoleName.PRIMARY: [str(primary_server)],
        ToolforgeToolsDBNodeRoleName.REPLICA: secondary_servers,
    }
