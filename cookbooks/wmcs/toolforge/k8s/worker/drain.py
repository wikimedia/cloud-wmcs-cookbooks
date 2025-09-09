r"""WMCS Toolforge - Drain a k8s worker node

Usage example:
    cookbook wmcs.toolforge.k8s.worker.drain \
        --cluster-name toolsbeta \
        --hostname-to-drain toolsbeta-test-worker-4
"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubernetes import KubernetesController

LOGGER = logging.getLogger(__name__)


class Drain(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_toolforge_kubernetes_cluster_opts(parser)
        parser.add_argument(
            "--hostname-to-drain",
            required=True,
            help="Hostname (without domain) of the node to drain.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            DrainRunner,
        )(
            hostname_to_drain=args.hostname_to_drain,
            spicerack=self.spicerack,
        )


class DrainRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        hostname_to_drain: str,
        spicerack: Spicerack,
    ):

        self.cluster_name = cluster_name
        self.hostname_to_drain = hostname_to_drain
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for node {self.hostname_to_drain}"

    def _pick_a_control_node(self) -> str:
        domain = f"{self.cluster_name.get_openstack_cluster_name()}.wikimedia.cloud"
        fqdn_to_drain = f"{self.hostname_to_drain}.{self.cluster_name.get_project()}.{domain}"
        LOGGER.debug("Finding next control node that is not %s", fqdn_to_drain)
        return next(
            control_node for control_node in get_control_nodes(self.cluster_name) if control_node != fqdn_to_drain
        )

    def run(self) -> None:

        remote = self.spicerack.remote()
        kubectl = KubernetesController(remote=remote, controlling_node_fqdn=self._pick_a_control_node())
        kubectl.drain_node(node_hostname=self.hostname_to_drain)
        kubectl.wait_for_drain(node_hostname=self.hostname_to_drain)
