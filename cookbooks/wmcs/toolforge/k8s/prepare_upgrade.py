r"""WMCS Toolforge Kubernetes - prepares a cluster for upgrading

Usage example:
    cookbook wmcs.toolforge.k8s.prepare_upgrade \
        --cluster-name toolsbeta \
        --src-version 1.22.17 \
        --dst-version 1.23.15

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation
from wmflib.requests import http_session

from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase
from wmcs_libs.inventory import ToolforgeKubernetesClusterName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubeadm import KUBEADM_VERSION_COMPONENT_HIERA_KEY, KUBERNETES_VERSION_HIERA_KEY
from wmcs_libs.k8s.kubernetes import KubernetesController
from wmcs_libs.openstack.enc import Enc

LOGGER = logging.getLogger(__name__)


class ToolforgeK8sPrepareUpgrade(CookbookBase):
    """Prepare a Kubernetes cluster for upgrades."""

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
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeK8sPrepareUpgradeRunner,)(
            spicerack=self.spicerack,
            src_version=args.src_version,
            dst_version=args.dst_version,
        )


class ToolforgeK8sPrepareUpgradeRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeK8sPrepareUpgrade."""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        src_version: str,
        dst_version: str,
        spicerack: Spicerack,
    ):
        """Init"""
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.src_version = src_version
        self.dst_version = dst_version

        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for cluster {self.cluster_name} upgrade from {self.src_version} to {self.dst_version}"

    def _format_apt_component(self) -> str:
        dst_parts = self.dst_version.split(".")
        return f"thirdparty/kubeadm-k8s-{dst_parts[0]}-{dst_parts[1]}"

    def _check_component_exists(self):
        component = self._format_apt_component()
        deb_name = f"kubeadm_{self.dst_version}-00_amd64.deb"
        url_to_check = f"https://apt.wikimedia.org/wikimedia/pool/{component}/k/kubeadm/{deb_name}"

        session = http_session("wmcs-cookbooks wmcs.toolforge.k8s.prepare_upgrade")
        if session.head(url_to_check).status_code != 200:
            raise Exception(f"Version {self.dst_version} does not seem to exist in the Wikimedia APT repository")

    def run(self) -> None:
        """Main entry point"""
        control_node_fqdn = get_control_nodes(self.cluster_name)[0]
        k8s_controller = KubernetesController(self.spicerack.remote(), control_node_fqdn)
        LOGGER.info("Using control node %s", control_node_fqdn)

        LOGGER.info("Validating inputs")
        self._check_component_exists()

        LOGGER.info("Querying node data")
        node_fqdns = [f"{node}.{k8s_controller.get_nodes_domain()}" for node in k8s_controller.get_nodes_hostnames()]
        hosts = self.spicerack.remote().query(f"D{{{','.join(node_fqdns)}}}", use_sudo=True)

        LOGGER.info("Disabling Puppet on all Kubernetes nodes")
        puppet = self.spicerack.puppet(hosts)
        puppet.disable(self.spicerack.admin_reason(f"kubernetes upgrade to {self.dst_version}"), verbatim_reason=True)

        LOGGER.info("Downtiming project on Alertmanager")
        LOGGER.info("The cookbook can't yet do this automatically, please manually add a downtime")
        # TODO: automate this
        ask_confirmation("Have you added a downtime to https://prometheus-alerts.wmcloud.org yet?")

        LOGGER.info("Updating Hiera key")
        enc = Enc(remote=self.spicerack.remote(), cluster_name=self.cluster_name.get_openstack_cluster_name())
        enc_prefix = enc.prefix(self.cluster_name.get_project(), Enc.PROJECT_PREFIX)

        enc_prefix.set_hiera_values(
            {
                KUBEADM_VERSION_COMPONENT_HIERA_KEY: self._format_apt_component(),
                KUBERNETES_VERSION_HIERA_KEY: self.dst_version,
            }
        )
