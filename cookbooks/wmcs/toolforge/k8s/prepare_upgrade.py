r"""WMCS Toolforge Kubernetes - prepares a cluster for upgrading

Usage example:
    cookbook wmcs.toolforge.k8s.prepare_upgrade \
        --cluster-name toolsbeta \
        --dst-version 1.23.15

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase
from wmflib.interactive import ask_confirmation
from wmflib.requests import http_session

from cookbooks.wmcs.toolforge.run_tests import ToolforgeRunTestsRunner
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubeadm import KUBEADM_VERSION_COMPONENT_HIERA_KEY, KUBERNETES_VERSION_HIERA_KEY
from wmcs_libs.k8s.kubernetes import KubernetesController, validate_version
from wmcs_libs.openstack.enc import Enc

LOGGER = logging.getLogger(__name__)


class ToolforgeK8sPrepareUpgrade(CookbookBase):
    """Prepare a Kubernetes cluster for upgrades."""

    def argument_parser(self):

        parser = super().argument_parser()
        add_toolforge_kubernetes_cluster_opts(parser)
        parser.add_argument(
            "--dst-version",
            required=True,
            type=validate_version,
            help="New version to migrate to (ex. 1.30.14).",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeK8sPrepareUpgradeRunner,
        )(
            spicerack=self.spicerack,
            dst_version=args.dst_version,
        )


class ToolforgeK8sPrepareUpgradeRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        dst_version: str,
        spicerack: Spicerack,
    ):

        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.dst_version = dst_version

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for cluster {self.cluster_name} upgrade to {self.dst_version}"

    def _format_apt_component(self) -> str:
        dst_parts = self.dst_version.split(".")
        return f"thirdparty/kubeadm-k8s-{dst_parts[0]}-{dst_parts[1]}"

    def _check_component_exists(self):
        session = http_session("wmcs-cookbooks wmcs.toolforge.k8s.prepare_upgrade")
        component = self._format_apt_component()
        url_to_check = f"https://apt.wikimedia.org/wikimedia/pool/{component}/k/kubeadm/"

        LOGGER.info("Checked URL %s", url_to_check)

        result = session.get(url_to_check)
        if result.status_code != 200:
            raise Exception(f"{self.dst_version} doesn't exists in the repo. Check reprepro and URL {url_to_check}")

        if self.dst_version not in result.text or ".deb" not in result.text:
            raise Exception(f"{component} has no .deb files. Check reprepro and URL {url_to_check}")

    def run(self) -> None:

        control_node_fqdn = get_control_nodes(self.cluster_name)[0]
        k8s_controller = KubernetesController(self.spicerack.remote(), control_node_fqdn)
        LOGGER.info("Using control node %s", control_node_fqdn)

        LOGGER.info("Validating inputs")
        self._check_component_exists()

        LOGGER.info("Ensuring functional tests pass")
        ToolforgeRunTestsRunner(
            common_opts=self.common_opts,
            cluster_name=self.cluster_name,
            spicerack=self.spicerack,
            filter_tags=[],
        ).run_with_proxy()

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
