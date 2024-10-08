r"""WMCS Toolforge - Depool and delete the given k8s worker node from a toolforge installation

Usage example:
    cookbook wmcs.toolforge.k8s.worker.depool_and_remove_node \
        --cluster-name toolsbeta \
        --role worker \
        --hostname-to-remove toolsbeta-test-worker-4 \
        --force

"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation

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
from wmcs_libs.k8s.kubernetes import KubernetesController, KubernetesNodeNotFound, KubernetesTimeoutForDrain
from wmcs_libs.openstack.common import OpenstackAPI
from wmcs_libs.openstack.enc import Enc

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
            help="Role of the node to remove",
        )
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="If passed, will remove the node even if the drain fails.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeDepoolAndRemoveNodeRunner,
        )(
            hostname_to_remove=args.hostname_to_remove,
            role=args.role,
            force=args.force,
            spicerack=self.spicerack,
        )


class ToolforgeDepoolAndRemoveNodeRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeDepoolAndRemoveNode"""

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        spicerack: Spicerack,
        hostname_to_remove: str,
        role: ToolforgeKubernetesNodeRoleName,
        force: bool,
    ):
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.hostname_to_remove = hostname_to_remove
        self.role = role
        self.force = force

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

    def _update_hiera(self):
        if not self.role.list_in_hiera:
            return

        hiera_role, hiera_key = self.role.list_in_hiera
        hiera_prefix = get_cluster_node_prefix(self.cluster_name, hiera_role)
        LOGGER.info("Updating Hiera key '%s' in prefix '%s'", hiera_key, hiera_prefix)

        enc = Enc(remote=self.spicerack.remote(), cluster_name=self.cluster_name.get_openstack_cluster_name())
        enc_prefix = enc.prefix(
            self.cluster_name.get_project(),
            hiera_prefix,
        )

        domain = f"{self.cluster_name.get_openstack_cluster_name()}.wikimedia.cloud"
        fqdn_to_remove = f"{self.hostname_to_remove}.{self.cluster_name.get_project()}.{domain}"

        hiera = enc_prefix.get_current_hiera()
        hiera[hiera_key] = [node for node in hiera[hiera_key] if node != fqdn_to_remove]
        enc_prefix.set_hiera_values(hiera)

        # TODO: should we manually run Puppet on the affected nodes?
        # Or is relying on HAProxy health checks fine enough?

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

        self._update_hiera()

        drain_cookbook = Drain(spicerack=self.spicerack)
        drain_args = [
            "--hostname-to-drain",
            self.hostname_to_remove,
            "--cluster-name",
            self.cluster_name.value,
        ]

        drain_cookbook_runner: WMCSCookbookRunnerBase = drain_cookbook.get_runner(
            args=drain_cookbook.argument_parser().parse_args(args=drain_args),
        )
        drain_cookbook_err: KubernetesTimeoutForDrain | None = None

        control_node_fqdn = self._pick_a_control_node()
        LOGGER.info("Found control node %s", control_node_fqdn)
        kubectl = KubernetesController(remote=remote, controlling_node_fqdn=control_node_fqdn)
        try:
            drain_cookbook_runner.run()
            kubectl.delete_node(self.hostname_to_remove)
        except KubernetesTimeoutForDrain as exc:
            evictable_pods = kubectl.get_evictable_pods_for_node(self.hostname_to_remove)
            log_msg = (
                f"Failed to drain node {self.hostname_to_remove}.\n"
                f"Still has {len(evictable_pods)} pod(s) running. Running pods:\n"
                + "\n".join([f"* {pod['metadata']['name']}" for pod in evictable_pods])
            )
            LOGGER.warning(log_msg)
            drain_cookbook_err = exc
        except KubernetesNodeNotFound:
            # ignore! this is OK
            pass

        if drain_cookbook_err:
            if self.force:
                LOGGER.info("Force flag passed, removing node %s anyway", self.hostname_to_remove)
            else:
                try:
                    ask_confirmation(
                        "Would you like to remove the node anyway "
                        "(use --force to skip this question and remove the node without interaction)?"
                    )
                except Exception as exc:
                    raise exc from drain_cookbook_err

        LOGGER.info("Removing k8s %s node %s...", self.role, self.hostname_to_remove)
        remove_instance_cookbook = RemoveInstance(spicerack=self.spicerack)
        remove_instance_cookbook.get_runner(
            args=remove_instance_cookbook.argument_parser().parse_args(
                [
                    "--server-name",
                    self.hostname_to_remove,
                ]
                + self.common_opts.to_cli_args(),
            ),
        ).run()
