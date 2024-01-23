r"""WMCS Toolforge - Depool and delete the given k8s worker node from a toolforge installation

Usage example:
    cookbook wmcs.toolforge.k8s.worker.depool_and_remove_node \
        --cluster-name toolsbeta \
        --hostname-to-remove toolsbeta-test-worker-4

"""
from __future__ import annotations

import argparse
import logging
from typing import Any

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from cookbooks.wmcs.toolforge.k8s.worker.drain import Drain
from cookbooks.wmcs.vps.remove_instance import RemoveInstance
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, natural_sort_key
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_cluster_node_prefix,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubernetes import KubernetesController, KubernetesNodeNotFound
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class ToolforgeDepoolAndRemoveNode(CookbookBase):
    """WMCS Toolforge cookbook to remove and delete an existing k8s worker node"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_toolforge_kubernetes_cluster_opts(parser)
        parser.add_argument(
            "--hostname-to-remove",
            required=False,
            default=None,
            help="Host name of the node to remove, if none passed will remove the instance with the lower index.",
        )
        parser.add_argument(
            "--role",
            required=True,
            choices=[role for role in ToolforgeKubernetesNodeRoleName if role.runs_kubelet],
            type=ToolforgeKubernetesNodeRoleName.from_str,
            default=ToolforgeKubernetesNodeRoleName.WORKER,
            help="Role of the node to remove",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeDepoolAndRemoveNodeRunner,)(
            hostname_to_remove=args.hostname_to_remove,
            role=args.role,
            spicerack=self.spicerack,
        )


class ToolforgeDepoolAndRemoveNodeRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeDepoolAndRemoveNode"""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        spicerack: Spicerack,
        hostname_to_remove: str,
        role: ToolforgeKubernetesNodeRoleName,
    ):
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.hostname_to_remove = hostname_to_remove
        self.role = role

        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=self.cluster_name.get_openstack_cluster_name(),
            project=self.cluster_name.get_project(),
        )
        self._all_project_servers: list[dict[str, Any]] | None = None

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        if self.hostname_to_remove:
            return f"for host {self.hostname_to_remove}"
        return ""

    def _get_oldest_node(self, name_prefix: str) -> str:
        if not self._all_project_servers:
            self._all_project_servers = self.openstack_api.server_list()

        prefix_members = list(
            sorted(
                (
                    server
                    for server in self._all_project_servers
                    if server.get("Name", "noname").startswith(name_prefix)
                ),
                key=lambda server: natural_sort_key(server.get("Name", "noname-0")),
            )
        )
        if not prefix_members:
            raise Exception(
                f"No servers in project {self.cluster_name.get_project()} with prefix {name_prefix}, nothing to remove."
            )

        return f"{prefix_members[0]['Name']}"

    def _pick_a_control_node(self) -> str:
        domain = f"{self.cluster_name.get_openstack_cluster_name()}.wikimedia.cloud"
        fqdn_to_remove = f"{self.hostname_to_remove}.{self.cluster_name.get_project()}.{domain}"
        LOGGER.debug("Finding next control node that is not %s", fqdn_to_remove)
        return next(
            control_node for control_node in get_control_nodes(self.cluster_name) if control_node != fqdn_to_remove
        )

    def run(self) -> None:
        """Main entry point"""
        remote = self.spicerack.remote()

        name_prefix = get_cluster_node_prefix(self.cluster_name, self.role)
        if self.hostname_to_remove:
            if not self.hostname_to_remove.startswith(name_prefix):
                raise Exception(
                    f"Host name {self.hostname_to_remove} does not start with prefix {name_prefix} as expected"
                    f" for {self.role} nodes"
                )
        else:
            self.hostname_to_remove = self._get_oldest_node(name_prefix)
            LOGGER.info("Picked node %s to remove.", self.hostname_to_remove)

        control_node_fqdn = self._pick_a_control_node()
        LOGGER.info("Found control node %s", control_node_fqdn)

        # TODO: if removing a control or ingress node, remove
        # it from haproxy hieradata and run puppet there

        drain_cookbook = Drain(spicerack=self.spicerack)
        drain_args = [
            "--hostname-to-drain",
            self.hostname_to_remove,
            "--control-node-fqdn",
            control_node_fqdn,
            "--no-dologmsg",  # not interested in the inner SAL entries
        ] + self.common_opts.to_cli_args()

        drain_cookbook.get_runner(args=drain_cookbook.argument_parser().parse_args(args=drain_args)).run()

        kubectl = KubernetesController(remote=remote, controlling_node_fqdn=control_node_fqdn)
        try:
            kubectl.delete_node(self.hostname_to_remove)
        except KubernetesNodeNotFound:
            # ignore! this is OK
            pass

        LOGGER.info("Removing k8s %s node %s...", self.role, self.hostname_to_remove)
        remove_instance_cookbook = RemoveInstance(spicerack=self.spicerack)
        remove_instance_cookbook.get_runner(
            args=remove_instance_cookbook.argument_parser().parse_args(
                [
                    "--server-name",
                    self.hostname_to_remove,
                    "--no-dologmsg",  # not interested in the inner SAL entry
                    "--revoke-puppet-certs",  # so it will also be removed from puppetdb
                ]
                + self.common_opts.to_cli_args(),
            ),
        ).run()
