r"""WMCS Toolforge - Drain a k8s worker node

Usage example:
    cookbook wmcs.toolforge.k8s.worker.drain \
        --control-node-fqdn toolsbeta-test-control-5.toolsbeta.eqiad1.wikimedia.cloud \
        --hostname-to-drain toolsbeta-test-worker-4
"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.k8s.kubernetes import KubernetesController

LOGGER = logging.getLogger(__name__)


class Drain(CookbookBase):
    """WMCS Toolforge cookbook to drain a k8s worker node"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser, project_default="toolsbeta")
        parser.add_argument(
            "--control-node-fqdn",
            required=True,
            help="FQDN of a control node in the cluster.",
        )
        parser.add_argument(
            "--hostname-to-drain",
            required=True,
            help="Hostname (without domain) of the node to drain.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, DrainRunner,)(
            hostname_to_drain=args.hostname_to_drain,
            control_node_fqdn=args.control_node_fqdn,
            spicerack=self.spicerack,
        )


class DrainRunner(WMCSCookbookRunnerBase):
    """Runner for Drain"""

    def __init__(
        self,
        common_opts: CommonOpts,
        hostname_to_drain: str,
        control_node_fqdn: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.control_node_fqdn = control_node_fqdn
        self.hostname_to_drain = hostname_to_drain
        super().__init__(spicerack=spicerack)
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )

    def run(self) -> None:
        """Main entry point"""
        remote = self.spicerack.remote()
        kubectl = KubernetesController(remote=remote, controlling_node_fqdn=self.control_node_fqdn)
        kubectl.drain_node(node_hostname=self.hostname_to_drain)
        kubectl.wait_for_drain(node_hostname=self.hostname_to_drain)
        self.sallogger.log(message=f"drained node {self.hostname_to_drain}")
