r"""WMCS Toolforge Kubernetes - reboot k8s workers that are stuck on NFS

Usage example:
    cookbook wmcs.toolforge.k8s.reboot_stuck_workers \
        --cluster-name tools

    cookbook wmcs.toolforge.k8s.reboot_stuck_workers \
        --cluster-name toolsbeta

"""

import argparse
import logging
from typing import cast

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation

from cookbooks.wmcs.toolforge.k8s.reboot import ToolforgeK8sRebootRunner
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase
from wmcs_libs.inventory.cluster import ClusterType, SiteName
from wmcs_libs.inventory.static import get_static_inventory
from wmcs_libs.inventory.toolsk8s import (
    ToolforgeKubernetesCluster,
    ToolforgeKubernetesClusterName,
)
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.openstack.common import OpenstackAPI, OpenstackClusterName
from wmcs_libs.prometheus import get_nodes_from_query

LOGGER = logging.getLogger(__name__)


class ToolforgeK8sRebootStuckWorkers(CookbookBase):
    """Reboot k8s workers that are stuck."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        parser.add_argument(
            "--yes-i-know-what-im-doing",
            required=False,
            action="store_true",
            help="If passed, will not ask for confirmation.",
        )
        add_toolforge_kubernetes_cluster_opts(parser)
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeK8sRebootStuckWorkersRunner,
        )(
            yes_i_know=args.yes_i_know_what_im_doing,
            spicerack=self.spicerack,
        )


class ToolforgeK8sRebootStuckWorkersRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeK8sReboot."""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        yes_i_know: bool,
        spicerack: Spicerack,
    ):
        """Init"""
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.yes_i_know = yes_i_know
        self.domain = f"{self.common_opts.project}.eqiad1.wikimedia.cloud"
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=OpenstackClusterName.EQIAD1,
            project=self.common_opts.project,
        )
        inventory = get_static_inventory()
        # this comes from the alert ToolforgeKubernetesWorkerTooManyDProcesses
        # https://gitlab.wikimedia.org/repos/cloud/toolforge/alerts/-/blob/main/kubernetes/worker_stuck.yaml?ref_type=heads
        self.query = 'avg_over_time(node_processes_state{instance=~"tools.*-k8s-worker-nfs.*",state="D"}[1h]) > 12'
        self.prometheus_url = cast(
            ToolforgeKubernetesCluster,
            inventory[SiteName.EQIAD].clusters_by_type[ClusterType.TOOLFORGE_KUBERNETES][cluster_name],
        ).prometheus_url
        self.hostname_list = get_nodes_from_query(query=self.query, prometheus_url=self.prometheus_url)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        if not self.hostname_list:
            return "no stuck workers found"
        return f"for {', '.join(self.hostname_list)}"

    def run_with_proxy(self) -> None:
        """Main entry point"""
        if not self.hostname_list:
            print(f"No stuck workers found, used query '{self.query}' on prometheus server {self.prometheus_url}")
            return

        if not self.yes_i_know:
            workers_str = "\n* ".join(self.hostname_list)
            ask_confirmation(f"Will reboot the workers:{workers_str}" + "\nAre you sure?")

        ToolforgeK8sRebootRunner(
            common_opts=self.common_opts,
            cluster_name=self.cluster_name,
            hostname_list=self.hostname_list,
            spicerack=self.spicerack,
            do_all=False,
            do_all_nfs_workers=False,
            do_all_workers=False,
        ).run_with_proxy()
