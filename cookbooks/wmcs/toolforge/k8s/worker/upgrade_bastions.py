r"""WMCS Toolforge Kubernetes - upgrade bastion nodes

Usage example:
    cookbook wmcs.toolforge.k8s.upgrade_bastions \
        --cluster-name tools

    cookbook wmcs.toolforge.k8s.upgrade_bastions \
        --cluster-name toolsbeta

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase
from wmflib.interactive import ask_confirmation

from wmcs_libs.common import (
    CUMIN_SAFE_WITH_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    CommonOpts,
    WMCSCookbookRunnerBase,
    run_one_raw,
)
from wmcs_libs.inventory.static import get_static_inventory
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import add_toolforge_kubernetes_cluster_opts, with_toolforge_kubernetes_cluster_opts

LOGGER = logging.getLogger(__name__)


class ToolforgeK8sUpgradeBastions(CookbookBase):
    """Upgrade k8s bastions"""

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
            help="Comma-separated list of bastions to upgrade, if not passed, it will all of them.",
        )
        add_toolforge_kubernetes_cluster_opts(parser)
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeK8sUpgradeBastionsRunner,
        )(
            yes_i_know=args.yes_i_know_what_im_doing,
            hosts=args.hosts,
            spicerack=self.spicerack,
        )


class ToolforgeK8sUpgradeBastionsRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        yes_i_know: bool,
        hosts: str,
        spicerack: Spicerack,
    ):
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.yes_i_know = yes_i_know
        self.remote = self.spicerack.remote()

        if hosts:
            self.hostname_list = [host.strip() for host in hosts.split(",")]
        else:
            site = cluster_name.get_openstack_cluster_name().get_site()
            self.hostname_list = [
                bastion
                for bastion in get_static_inventory()[site]
                .clusters_by_type[cluster_name.get_type()][cluster_name]
                .nodes_by_role[ToolforgeKubernetesNodeRoleName.BASTION]
                # skip old bastion
                if "sge" not in bastion
            ]

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        if not self.hostname_list:
            return "no basations to upgrade, maybe failed to list them :/"
        return f"for {', '.join(self.hostname_list)}"

    def run_with_proxy(self) -> None:
        if not self.hostname_list:
            print("No bastion nodes to upgrade found, maybe failed to get from the cluster.")
            return

        if not self.yes_i_know:
            hosts_str = "\n* " + "\n* ".join(self.hostname_list)
            ask_confirmation(f"Upgrading bastions: {hosts_str}\nAre you sure?")

        # We expect to pull the latest from the repos, not a specific version
        for index, hostname in enumerate(self.hostname_list):
            bastion_node = self.remote.query(f"D{{{hostname}}}", use_sudo=True)
            run_one_raw(
                command=["apt", "full-upgrade", "--yes"],
                node=bastion_node,
                cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
            )
            print(
                f"## upgrade_bastions: Upgraded {index + 1} of {len(self.hostname_list)} nodes, "
                f"{len(self.hostname_list) - index - 1} left"
            )

        print("Upgraded kubectl to versions:")
        for index, hostname in enumerate(self.hostname_list):
            bastion_node = self.remote.query(f"D{{{hostname}}}", use_sudo=True)
            run_one_raw(
                command=["apt", "policy", "kubectl"],
                node=bastion_node,
                cumin_params=CUMIN_SAFE_WITH_OUTPUT,
            )
