r"""WMCS Toolforge Kubernetes - reboot nodes

Usage example:
    cookbook wmcs.toolforge.k8s.reboot \
        --cluster-name tools \
        --hostname-list tools-k8s-control-1 tools-k8s-worker-3

    cookbook wmcs.toolforge.k8s.reboot \
        --cluster-name toolsbeta \
        --all

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, parser_type_list_hostnames
from wmcs_libs.inventory import ToolforgeKubernetesClusterName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubernetes import KubernetesController

LOGGER = logging.getLogger(__name__)


class ToolforgeK8sReboot(CookbookBase):
    """Reboot k8s nodes."""

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
            "--hostname-list",
            required=False,
            nargs="+",
            type=parser_type_list_hostnames,
            help="list of k8s nodes to operate on",
        )
        parser.add_argument(
            "--all",
            required=False,
            action="store_true",
            help="operate on all cluster nodes",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeK8sRebootRunner,)(
            spicerack=self.spicerack,
            hostname_list=args.hostname_list,
            do_all=args.all,
        )


class ToolforgeK8sRebootRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeK8sReboot."""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        hostname_list: list[str],
        do_all: bool,
        spicerack: Spicerack,
    ):
        """Init"""
        super().__init__(spicerack=spicerack)
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.hostname_list = hostname_list
        self.do_all = do_all
        self.domain = f"{self.common_opts.project}.eqiad1.wikimedia.cloud"
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )

        if do_all and hostname_list:
            raise Exception("--all and --hostname-list are mutually exclusive")

        if not do_all and not hostname_list:
            raise Exception("either --all or --hostname-list needs to be specified")

    def _select_k8s_controller(self) -> None:
        """Select a k8s control node."""

    def run(self) -> None:
        """Main entry point"""
        control_node_fqdn = get_control_nodes(self.cluster_name)[0]
        k8s_controller = KubernetesController(self.spicerack.remote(), control_node_fqdn)
        LOGGER.info("INFO: using control node %s", control_node_fqdn)

        if self.do_all:
            # in reverse order, so the controllers are done last!
            self.hostname_list = k8s_controller.get_nodes_hostnames()[::-1]
            self.sallogger.log(
                f"rebooting the whole {self.common_opts.project} k8s cluster ({len(self.hostname_list)} nodes)"
            )

        for node_hostname in self.hostname_list:
            for phase in k8s_controller.reboot_node(node_hostname, self.domain):
                LOGGER.info("INFO: %s: reboot phase: %s", node_hostname, phase)

            self.sallogger.log(f"rebooted k8s node {node_hostname}")
