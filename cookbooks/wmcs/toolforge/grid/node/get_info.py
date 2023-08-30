r"""WMCS Toolforge - grid - node - get info

Usage example:
    cookbook wmcs.toolforge.grid.node.get_info \
        --project toolsbeta \
        --master-node-fqdn toolsbeta-test-etcd-8.toolsbeta.eqiad1.wikimedia.cloud
"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.grid import GridController, GridNodeNotFound

LOGGER = logging.getLogger(__name__)


class ToolforgeGridNodeGetInfo(CookbookBase):
    """Toolforge cookbook to get information about a grid node"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--master-node-fqdn",
            required=False,
            default=None,
            help=(
                "Name of the grid master node, will use <project>-sgegrid-master.<project>.eqiad1.wikimedia.cloud by "
                "default."
            ),
        )
        parser.add_argument("--node-hostname", required=True, help="short hostname of the node.")

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, ToolforgeGridNodeGetInfoRunner)(
            master_node_fqdn=args.master_node_fqdn
            or f"{args.project}-sgegrid-master.{args.project}.eqiad1.wikimedia.cloud",
            spicerack=self.spicerack,
            node_hostname=args.node_hostname,
        )


class ToolforgeGridNodeGetInfoRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeGridNodeGetInfo"""

    def __init__(
        self,
        common_opts: CommonOpts,
        master_node_fqdn: str,
        spicerack: Spicerack,
        node_hostname: str,
    ):
        """Init"""
        self.master_node_fqdn = master_node_fqdn
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.node_hostname = node_hostname

    def run(self) -> int | None:
        """Main entry point"""
        grid_controller = GridController(remote=self.spicerack.remote(), master_node_fqdn=self.master_node_fqdn)
        try:
            print(grid_controller.get_node_info(self.node_hostname))
        except GridNodeNotFound as e:
            print(f"ERROR: {e}")
            return 1
        return 0
