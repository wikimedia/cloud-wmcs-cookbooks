"""WMCS Toolforge - Upgrade a Kubernetes worker node

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
from spicerack.cookbook import CookbookBase
from spicerack.decorators import retry
from wmflib.interactive import ask_confirmation

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, run_one_raw
from wmcs_libs.inventory.libs import NodeInventoryInfo, get_node_inventory_info
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import add_toolforge_kubernetes_cluster_opts, with_toolforge_kubernetes_cluster_opts
from wmcs_libs.k8s.kubeadm import KubeadmController
from wmcs_libs.k8s.kubernetes import KubeletController, KubernetesController, validate_version

LOGGER = logging.getLogger(__name__)


class Upgrade(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_toolforge_kubernetes_cluster_opts(parser)
        parser.add_argument(
            "--hostname",
            required=True,
            help="Host name of the node to upgrade.",
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

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            UpgradeRunner,
        )(
            hostname=args.hostname,
            src_version=args.src_version,
            dst_version=args.dst_version,
            spicerack=self.spicerack,
        )


class UpgradeRunner(WMCSCookbookRunnerBase):

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        spicerack: Spicerack,
        hostname: str,
        src_version: str | None,
        dst_version: str,
    ):

        self.common_opts = common_opts
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.hostname = hostname
        self.dst_version = dst_version

        self.remote = self.spicerack.remote()
        self.control_node_fqdn = KubernetesController.pick_a_control_node(
            cluster_name=self.cluster_name, skip_hostname=self.hostname
        )
        LOGGER.info("Using control node %s", self.control_node_fqdn)
        self.kubectl = KubernetesController(remote=self.remote, controlling_node_fqdn=self.control_node_fqdn)
        self.original_node_info = self.kubectl.get_node_info(self.hostname)

        self.src_version = src_version if src_version is not None else self.original_node_info.kubelet_version

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for node {self.hostname} from {self.src_version} to {self.dst_version}"

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

    def run_with_proxy(self) -> None:

        if self.src_version and self.original_node_info.kubelet_version != self.src_version:
            LOGGER.error(
                "Node %s has unexpected version %s (instead of %s), skipping",
                self.hostname,
                self.original_node_info.kubelet_version,
                self.src_version,
            )
            return

        if self.src_version == self.dst_version:
            LOGGER.warning(
                "Node %s is already in the destination version %s, skipping",
                self.hostname,
                self.dst_version,
            )
            return

        domain = f"{self.cluster_name.get_openstack_cluster_name()}.wikimedia.cloud"
        fqdn = f"{self.hostname}.{self.cluster_name.get_project()}.{domain}"

        inventory_info = get_node_inventory_info(fqdn)

        hosts = self.remote.query(f"D{{{fqdn}}}", use_sudo=True)

        LOGGER.info("Using control node %s", self.control_node_fqdn)

        kubeadm = KubeadmController(remote=self.remote, target_node_fqdn=fqdn)
        puppet = self.spicerack.puppet(hosts)
        apt_get = self.spicerack.apt_get(hosts)

        LOGGER.info("Draining node %s", self.hostname)
        self.kubectl.drain_node(node_hostname=self.hostname)

        LOGGER.info("Running Puppet on %s to pick up updated Apt components", self.hostname)

        puppet.enable(self.spicerack.admin_reason(f"kubernetes upgrade to {self.dst_version}"), verbatim_reason=True)
        puppet.run()

        if self.src_version.split(".")[:-1] == self.dst_version.split(".")[:-1]:
            # If updating to a new patch version of the same minor release, the component
            # says the same. So manually refresh the Apt cache in those cases.
            LOGGER.info("Updating apt index cache")
            apt_get.update()

        LOGGER.info("Installing updated kubeadm package on %s", self.hostname)
        apt_get.install("kubeadm")

        LOGGER.info("Waiting for drain of %s to complete", self.hostname)
        self.kubectl.wait_for_drain(node_hostname=self.hostname)

        # TODO: this would be a perfect opportunity to apply any pending kernel updates

        LOGGER.info("Upgrading the node data")

        # For the first control node this is a bit more complicated.
        if self._is_first_node(kubectl=self.kubectl, inventory_info=inventory_info):
            kubeadm.upgrade_first(self.dst_version)
        else:
            kubeadm.upgrade()

        LOGGER.info("Upgrading packages")

        packages = ["kubectl", "kubelet", "containerd"]

        if inventory_info.role_name == ToolforgeKubernetesNodeRoleName.CONTROL:
            packages.append("helm")

        apt_get.install(*packages)

        LOGGER.info("Restarting kubelet")
        run_one_raw(
            command=["systemctl", "restart", "kubelet.service"],
            node=hosts,
        )

        LOGGER.info("Waiting for node to update api server")
        self._ensure_new_version(kubectl=self.kubectl)

        LOGGER.info("Uncordoning node")
        self.kubectl.uncordon_node(node_hostname=self.hostname)

        # TODO: for control nodes, tail service logs and ensure everything works
        if inventory_info.role_name == ToolforgeKubernetesNodeRoleName.CONTROL:
            # most likely, we need to restart static pods. It is known that controller-manager and/or scheduler
            # can show errors if they are started before the api-server by the kubelet. This is solved by another
            # manual restart in the right order, which this function should do
            kubelet = KubeletController(remote=self.remote, kubelet_node_fqdn=fqdn, k8s_control=self.kubectl)
            LOGGER.info("Restarting static pods in kube-system namespace")
            kubelet.restart_all_static_pods(namespace="kube-system")

            ask_confirmation("As this is a control node, please check that control plane services work fine")
