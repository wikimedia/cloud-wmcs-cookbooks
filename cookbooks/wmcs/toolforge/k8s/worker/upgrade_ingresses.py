r"""WMCS Toolforge Kubernetes - upgrade ingress k8s workers

Usage example:
    cookbook wmcs.toolforge.k8s.worker.upgrade_ingresses \
        --cluster-name tools

    cookbook wmcs.toolforge.k8s.worker.upgrade_ingresses \
        --cluster-name toolsbeta

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase
from wmflib.interactive import ask_confirmation

from cookbooks.wmcs.toolforge.k8s.worker.upgrade import UpgradeRunner
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName
from wmcs_libs.k8s.clusters import add_toolforge_kubernetes_cluster_opts, with_toolforge_kubernetes_cluster_opts
from wmcs_libs.k8s.kubernetes import KubernetesController, validate_version

LOGGER = logging.getLogger(__name__)

INGRESS_DEPLOYMENT = "ingress-nginx-gen2-controller"
INGRESS_NAMESPACE = "ingress-nginx-gen2"


class ToolforgeK8sUpgradeIngresses(CookbookBase):
    """Upgrade k8s ingresses"""

    def argument_parser(self):
        parser = super().argument_parser()
        parser.add_argument(
            "--yes-i-know-what-im-doing",
            required=False,
            action="store_true",
            help="If passed, will not ask for confirmation.",
        )
        parser.add_argument(
            "--hosts",
            required=False,
            default="",
            help="Comma-separated list of ingresses to upgrade, if not passed, it will all of them.",
        )
        parser.add_argument(
            "--src-version",
            required=False,
            type=validate_version,
            help="Old version to upgrade from, will autodetect if not passed.",
        )
        parser.add_argument(
            "--dst-version",
            required=True,
            type=validate_version,
            help="New version to migrate to.",
        )
        add_toolforge_kubernetes_cluster_opts(parser)
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeK8sUpgradeIngressesRunner,
        )(
            yes_i_know=args.yes_i_know_what_im_doing,
            hosts=args.hosts,
            src_version=args.src_version,
            dst_version=args.dst_version,
            spicerack=self.spicerack,
        )


class ToolforgeK8sUpgradeIngressesRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        yes_i_know: bool,
        hosts: str,
        src_version: str,
        dst_version: str,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.yes_i_know = yes_i_know
        self.src_version = src_version
        self.dst_version = dst_version

        self.remote = self.spicerack.remote()
        self.control_node_fqdn = KubernetesController.pick_a_control_node(cluster_name=self.cluster_name)
        LOGGER.info("Using control node %s", self.control_node_fqdn)
        self.k8s_controller = KubernetesController(remote=self.remote, controlling_node_fqdn=self.control_node_fqdn)

        if hosts:
            self.hostname_list = [host.strip() for host in hosts.split(",")]
        else:
            all_nodes = self.k8s_controller.get_nodes_hostnames()
            self.hostname_list = [host for host in all_nodes if "-ingress" in host]

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        if not self.hostname_list:
            return "no ingress nodes to upgrade found, maybe failed to get from the cluster."
        return f"for {', '.join(self.hostname_list)}"

    def run_with_proxy(self) -> None:
        if not self.hostname_list:
            print("No ingress nodes to upgrade found, maybe failed to get from the cluster.")
            return

        if not self.yes_i_know:
            hosts_str = "\n* " + "\n* ".join(self.hostname_list)
            ask_confirmation(f"Upgrading ingresses: {hosts_str}\nAre you sure?")

        # this is to avoid it getting scheduled in a regular worker during upgrade
        self.scale_ingress(replicas=2)

        for index, hostname in enumerate(self.hostname_list):
            UpgradeRunner(
                cluster_name=self.cluster_name,
                spicerack=self.spicerack,
                common_opts=self.common_opts,
                dst_version=self.dst_version,
                src_version=self.src_version,
                hostname=hostname,
            ).run_with_proxy()
            print(
                f"## upgrade_ingresses: Upgraded {index + 1} of {len(self.hostname_list)} nodes, "
                f"{len(self.hostname_list) - index - 1} left"
            )

        self.scale_ingress(replicas=3)

    def scale_ingress(self, replicas: int) -> None:
        self.k8s_controller.scale_deployment(
            deployment=INGRESS_DEPLOYMENT, new_replicas=replicas, namespace=INGRESS_NAMESPACE
        )
        self.k8s_controller.wait_for_deployment_replicas(
            deployment=INGRESS_DEPLOYMENT, namespace=INGRESS_NAMESPACE, num_replicas=replicas
        )
