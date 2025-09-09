r"""WMCS Toolforge - Remove HAProxy node from Toolforge Kubernetes clsuter.

Usage example:
    cookbook wmcs.toolforge.remove_k8s_haproxy_node \
        --cluster-name toolsbeta \
        --hostname-to-remove toolsbeta-test-k8s-haproxy-0

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.vps.remove_instance import RemoveInstance
from wmcs_libs.common import CUMIN_UNSAFE_WITH_OUTPUT, CommonOpts, WMCSCookbookRunnerBase, run_one_raw
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_cluster_node_prefix,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubeadm import HAPROXY_KEEPALIVED_PEERS_HIERA_KEY
from wmcs_libs.openstack.common import OpenstackAPI
from wmcs_libs.openstack.enc import Enc

LOGGER = logging.getLogger(__name__)


class ToolforgeRemoveK8sHaproxyNode(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_toolforge_kubernetes_cluster_opts(parser)
        parser.add_argument(
            "--hostname-to-remove",
            required=True,
            help="Host name of the node to remove.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeRemoveK8sHaproxyNodeRunner,
        )(
            spicerack=self.spicerack,
            hostname_to_remove=args.hostname_to_remove,
        )


class ToolforgeRemoveK8sHaproxyNodeRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        spicerack: Spicerack,
        hostname_to_remove: str,
    ):

        self.common_opts = common_opts
        self.cluster_name = cluster_name

        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=self.cluster_name.get_openstack_cluster_name(),
            project=self.cluster_name.get_project(),
        )

        self.hostname_to_remove = hostname_to_remove
        domain = f"{self.cluster_name.get_openstack_cluster_name()}.wikimedia.cloud"
        self.fqdn_to_remove = f"{self.hostname_to_remove}.{self.cluster_name.get_project()}.{domain}"

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for node {self.hostname_to_remove}"

    def _update_enc_node_list(self, hiera_prefix: str):
        enc = Enc(remote=self.spicerack.remote(), cluster_name=self.cluster_name.get_openstack_cluster_name())
        enc_prefix = enc.prefix(
            self.cluster_name.get_project(),
            hiera_prefix,
        )

        current_hiera = enc_prefix.get_current_hiera()
        current_hiera[HAPROXY_KEEPALIVED_PEERS_HIERA_KEY] = [
            entry for entry in current_hiera[HAPROXY_KEEPALIVED_PEERS_HIERA_KEY] if entry != self.fqdn_to_remove
        ]
        enc_prefix.set_hiera_values(current_hiera)

    def run(self) -> None:

        remote = self.spicerack.remote().query(f"D{{{self.fqdn_to_remove}}}", use_sudo=True)

        LOGGER.info("Disabling Puppet so that Keepalived does not re-start")
        puppet = self.spicerack.puppet(remote)
        puppet.disable(self.spicerack.admin_reason("host is being removed"))

        LOGGER.info("Removing node from configuration")
        self._update_enc_node_list(
            hiera_prefix=get_cluster_node_prefix(self.cluster_name, ToolforgeKubernetesNodeRoleName.HAPROXY)
        )

        LOGGER.info("Stopping keepalived")
        run_one_raw(
            node=remote, command=["systemctl", "stop", "keepalived.service"], cumin_params=CUMIN_UNSAFE_WITH_OUTPUT
        )

        LOGGER.info("Removing node")
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
