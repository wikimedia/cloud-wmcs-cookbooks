r"""WMCS Toolforge Kubernetes - restart static pods on a given k8s node

Usage example:
    cookbook wmcs.toolforge.k8s.restart_static_pods \
        --project tools \
        --hostname tools-k8s-something-1

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, parser_type_str_hostname
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubernetes import KubeletController, KubernetesController

LOGGER = logging.getLogger(__name__)

KUBERNETES_STATIC_POD_DIR = "/etc/kubernetes/manifests/"
KUBELET_CONFIG_FILE = "/var/lib/kubelet/config.yaml"


class ToolforgeK8sRestartStaticPods(CookbookBase):
    """Restart toolforge k8s static pods."""

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
            "--hostname",
            required=True,
            type=parser_type_str_hostname,
            help="k8s node to operate on",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeK8sRestartStaticPodsRunner,)(
            spicerack=self.spicerack,
            hostname=args.hostname,
        )


class ToolforgeK8sRestartStaticPodsRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeK8sRestartStaticPods."""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        hostname: str,
        spicerack: Spicerack,
    ):
        """Init"""
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.hostname = hostname

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for {self.hostname}"

    def run(self) -> None:
        """Main entry point"""
        remote = self.spicerack.remote()
        k8s_control = KubernetesController(remote=remote, controlling_node_fqdn=get_control_nodes(self.cluster_name)[0])
        node_fqdn = f"{self.hostname}.{self.common_opts.project}.eqiad1.wikimedia.cloud"
        kubelet = KubeletController(remote=remote, kubelet_node_fqdn=node_fqdn, k8s_control=k8s_control)
        kubelet.restart_all_static_pods(namespace="kube-system")
