r"""WMCS Toolforge - Upgrade a Kubernetes worker node

Usage example:
    cookbook wmcs.toolforge.k8s.worker.upgrade \
        --cluster-name toolsbeta \
        --hostname toolsbeta-test-worker-4 \
        --src-version 1.22.17 \
        --dst-version 1.23.15

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from spicerack.decorators import retry
from wmflib.interactive import ask_confirmation

from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, run_one_raw
from wmcs_libs.inventory import (
    NodeInventoryInfo,
    ToolforgeKubernetesClusterName,
    ToolforgeKubernetesNodeRoleName,
    get_node_inventory_info,
)
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubeadm import KubeadmController
from wmcs_libs.k8s.kubernetes import KubernetesController

LOGGER = logging.getLogger(__name__)


class Upgrade(CookbookBase):
    """WMCS Toolforge cookbook to upgrade a k8s worker node"""

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
            help="Host name of the node to upgrade.",
        )
        parser.add_argument(
            "--src-version",
            required=True,
            help="Old version to upgrade from.",
        )
        parser.add_argument(
            "--dst-version",
            required=True,
            help="New version to migrate to.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, UpgradeRunner,)(
            hostname=args.hostname,
            src_version=args.src_version,
            dst_version=args.dst_version,
            spicerack=self.spicerack,
        )


class UpgradeRunner(WMCSCookbookRunnerBase):
    """Runner for Upgrade"""

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        spicerack: Spicerack,
        hostname: str,
        src_version: str,
        dst_version: str,
    ):
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.hostname = hostname
        self.src_version = src_version
        self.dst_version = dst_version

        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for node {self.hostname} from {self.src_version} to {self.dst_version}"

    def _pick_a_control_node(self) -> str:
        domain = f"{self.cluster_name.get_openstack_cluster_name()}.wikimedia.cloud"
        fqdn = f"{self.hostname}.{self.cluster_name.get_project()}.{domain}"
        LOGGER.debug("Finding next control node that is not %s", fqdn)
        return next(control_node for control_node in get_control_nodes(self.cluster_name) if control_node != fqdn)

    def _format_kubeadm_config_map(self) -> str:
        dst_parts = self.dst_version.split(".")
        return f"kubelet-config-{dst_parts[0]}.{dst_parts[1]}"

    def _is_first_node(self, kubectl: KubernetesController, inventory_info: NodeInventoryInfo) -> bool:
        if inventory_info.role_name != ToolforgeKubernetesNodeRoleName.CONTROL:
            return False

        # TODO: kubeadm as of 1.23 logs a warning like this:
        # NOTE: The "kubelet-config-1.23" naming of the kubelet ConfigMap is deprecated. Once the
        # UnversionedKubeletConfigMap feature gate graduates to Beta the default name will become just
        # "kubelet-config". Kubeadm upgrade will handle this transition transparently.
        # So this mechanism will need to be updated soon-ish.

        return (
            kubectl.get_object(
                "configmaps", self._format_kubeadm_config_map(), namespace="kube-system", missing_ok=True
            )
            is None
        )

    @retry(
        tries=10,
        backoff_mode="power",
        failure_message="Node still has old version in Kubernetes API",
        exceptions=(RuntimeError,),
    )
    def _ensure_new_version(self, kubectl: KubernetesController):
        node_info = kubectl.get_node_info(self.hostname)
        if node_info.kubelet_version != self.dst_version:
            raise RuntimeError(f"Found unexpected version {node_info.kubelet_version} (instead of {self.dst_version})")

    def run(self) -> None:
        """Main entry point"""
        remote = self.spicerack.remote()

        domain = f"{self.cluster_name.get_openstack_cluster_name()}.wikimedia.cloud"
        fqdn = f"{self.hostname}.{self.cluster_name.get_project()}.{domain}"

        inventory_info = get_node_inventory_info(fqdn)

        hosts = remote.query(f"D{{{fqdn}}}", use_sudo=True)

        control_node_fqdn = self._pick_a_control_node()
        LOGGER.info("Using control node %s", control_node_fqdn)

        kubectl = KubernetesController(remote=remote, controlling_node_fqdn=control_node_fqdn)
        kubeadm = KubeadmController(remote=remote, target_node_fqdn=fqdn)
        puppet = self.spicerack.puppet(hosts)
        apt_get = self.spicerack.apt_get(hosts)

        original_node_info = kubectl.get_node_info(self.hostname)
        if original_node_info.kubelet_version != self.src_version:
            LOGGER.error(
                "Node %s has unexpected version %s (instead of %s), skipping",
                self.hostname,
                original_node_info.kubelet_version,
                self.src_version,
            )
            return

        LOGGER.info("Draining node %s", self.hostname)
        kubectl.drain_node(node_hostname=self.hostname)

        LOGGER.info("Running Puppet on %s to pick up updated Apt components", self.hostname)

        puppet.enable(self.spicerack.admin_reason(f"kubernetes upgrade to {self.dst_version}"))
        puppet.run()

        if self.src_version.split(".")[:-1] == self.dst_version.split(".")[:-1]:
            # If updating to a new patch version of the same minor release, the component
            # says the same. So manually refresh the Apt cache in those cases.
            LOGGER.info("Updating apt index cache")
            apt_get.update()

        LOGGER.info("Installing updated kubeadm package on %s", self.hostname)
        apt_get.install("kubeadm")

        LOGGER.info("Waiting for drain of %s to complete", self.hostname)
        kubectl.wait_for_drain(node_hostname=self.hostname)

        # TODO: this would be a perfect opportunity to apply any pending kernel updates

        LOGGER.info("Upgrading the node data")

        # For the first control node this is a bit more complicated.
        if self._is_first_node(kubectl=kubectl, inventory_info=inventory_info):
            kubeadm.upgrade_first(self.dst_version)
        else:
            kubeadm.upgrade()

        LOGGER.info("Upgrading packages")

        packages = ["kubectl", "kubelet", "containerd.io"]

        if inventory_info.role_name == ToolforgeKubernetesNodeRoleName.CONTROL:
            packages.append("helm")

        # TODO: only update when on Buster hosts
        packages.extend(["docker-ce", "docker-ce-cli"])

        apt_get.install(*packages)

        LOGGER.info("Restarting kubelet")
        run_one_raw(
            command=["systemctl", "restart", "kubelet.service"],
            node=hosts,
        )

        LOGGER.info("Waiting for node to update api server")
        self._ensure_new_version(kubectl=kubectl)

        LOGGER.info("Uncordoning node")
        kubectl.uncordon_node(node_hostname=self.hostname)

        # TODO: for control nodes, tail service logs and ensure everything works
        if inventory_info.role_name == ToolforgeKubernetesNodeRoleName.CONTROL:
            ask_confirmation("As this is a control node, please check that control plane services work fine")
