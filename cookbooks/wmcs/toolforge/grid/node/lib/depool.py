r"""WMCS Toolforge - grid - depool an existing grid exec/web node from the cluster

Usage example:
    cookbook wmcs.toolforge.grid.node.lib.depool \
        --project toolsbeta \
        --node-hostnames toolsbeta-sgewebgen-09-2 toolsbeta-sgeexec-10-1
"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CommonOpts,
    WMCSCookbookRunnerBase,
    add_common_opts,
    parser_type_list_hostnames,
    with_common_opts,
)
from wmcs_libs.grid import GridController, GridNodeNotFound
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class ToolforgeGridNodeDepool(CookbookBase):
    """WMCS Toolforge cookbook to depool a grid node"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        add_common_opts(parser, project_default="toolsbeta")
        parser.add_argument(
            "--grid-master-fqdn",
            required=False,
            default=None,
            help=(
                "FQDN of the grid master, will use <project>-sgegrid-master.<project>.eqiad1.wikimedia.cloud by "
                "default."
            ),
        )
        parser.add_argument(
            "--node-hostnames", required=True, help="FQDN of the new node.", nargs="+", type=parser_type_list_hostnames
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, ToolforgeGridNodeDepoolRunner,)(
            grid_master_fqdn=args.grid_master_fqdn
            or f"{args.project}-sgegrid-master.{args.project}.eqiad1.wikimedia.cloud",
            node_hostnames=args.node_hostnames,
            spicerack=self.spicerack,
        )


class ToolforgeGridNodeDepoolRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeGridNodeDepool."""

    def __init__(
        self,
        common_opts: CommonOpts,
        node_hostnames: list[str],
        grid_master_fqdn: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.grid_master_fqdn = grid_master_fqdn
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.node_hostnames = node_hostnames

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for {', '.join(self.node_hostnames)}"

    def run(self) -> int | None:
        """Main entry point"""
        openstack_api = OpenstackAPI(
            remote=self.spicerack.remote(),
            cluster_name=OpenstackClusterName.EQIAD1,
            project=self.common_opts.project,
        )

        grid_controller = GridController(remote=self.spicerack.remote(), master_node_fqdn=self.grid_master_fqdn)

        for node in self.node_hostnames:
            if not openstack_api.server_exists(node, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT):
                LOGGER.warning("node %s is not a VM in project %s", node, self.common_opts.project)
                return 1

            try:
                grid_controller.depool_node(host_fqdn=node)
            except GridNodeNotFound:
                LOGGER.warning("node %s not found in the %s grid", node, self.common_opts.project)
                return 1

        return 0
