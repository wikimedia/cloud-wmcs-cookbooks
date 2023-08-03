r"""WMCS Toolforge - grid - pool an existing grid exec/web node into the cluster

Usage example:
    cookbook wmcs.toolforge.grid.node.lib.pool \
        --project toolsbeta \
        --nodes-query toolsbeta-sgewebgen-09-[2-4],toolsbeta-sgeexec-10-[10,20]
"""
from __future__ import annotations

import argparse
import logging

from ClusterShell.NodeSet import NodeSetParseError
from cumin.backends import InvalidQueryError
from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CommonOpts,
    SALLogger,
    WMCSCookbookRunnerBase,
    add_common_opts,
    with_common_opts,
)
from wmcs_libs.grid import GridController, GridNodeNotFound
from wmcs_libs.inventory import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class ToolforgeGridNodePool(CookbookBase):
    """WMCS Toolforge cookbook to pool a grid node"""

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
            "--nodes-query",
            required=True,
            help="FQDN of the new node.",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, ToolforgeGridNodePoolRunner,)(
            grid_master_fqdn=args.grid_master_fqdn
            or f"{args.project}-sgegrid-master.{args.project}.eqiad1.wikimedia.cloud",
            nodes_query=args.nodes_query,
            spicerack=self.spicerack,
        )


class ToolforgeGridNodePoolRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeGridNodePool."""

    def __init__(
        self,
        common_opts: CommonOpts,
        nodes_query: str,
        grid_master_fqdn: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.grid_master_fqdn = grid_master_fqdn
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.nodes_query = nodes_query
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )

    def run(self) -> int | None:
        """Main entry point"""
        try:
            remote_hosts = self.spicerack.remote().query(f"D{{{self.nodes_query}}}")
            requested_nodes = remote_hosts.hosts
        except InvalidQueryError as exc:
            LOGGER.error("invalid query: %s", exc)
            return 1
        except NodeSetParseError as exc:
            LOGGER.error("invalid query: %s", exc)
            return 1

        openstack_api = OpenstackAPI(
            remote=self.spicerack.remote(), cluster_name=OpenstackClusterName.EQIAD1, project=self.common_opts.project
        )

        actual_nodes = openstack_api.server_list_filter_exists(
            requested_nodes[:], cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT
        )

        for node in set(requested_nodes) - set(actual_nodes):
            LOGGER.warning("node %s is not a VM in project %s, ignoring", node, self.common_opts.project)

        _grid_controller = GridController(remote=self.spicerack.remote(), master_node_fqdn=self.grid_master_fqdn)

        counter = 0
        for hostname in actual_nodes:
            if self.spicerack.dry_run:
                LOGGER.info("would repool node %s", hostname)
                counter += 1
                continue

            try:
                _grid_controller.pool_node(hostname=hostname)
                LOGGER.info("repooled node %s", hostname)
                counter += 1
            except GridNodeNotFound:
                LOGGER.warning("node %s not found in the %s grid, ignoring", hostname, self.common_opts.project)

        if counter > 0:
            self.sallogger.log(message=f"pooled {counter} grid nodes {self.nodes_query}")
            return 0

        LOGGER.error("couldn't pool any node")
        return 1
