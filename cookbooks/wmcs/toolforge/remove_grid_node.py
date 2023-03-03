r"""WMCS Toolforge - grid - removes a worker node

Usage example:
    cookbook wmcs.toolforge.remove_grid_node \
        --project toolsbeta \
        --master-node-fqdn toolsbeta-sgegrid-master.toolsbeta.eqiad1.wikimedia.cloud \
        --node-hostnames toolsbeta-sgeexec-0901
"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.vps.remove_instance import RemoveInstance
from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CommonOpts,
    SALLogger,
    WMCSCookbookRunnerBase,
    add_common_opts,
    parser_type_list_hostnames,
    with_common_opts,
)
from wmcs_libs.grid import GridController, GridNodeNotFound
from wmcs_libs.inventory import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class ToolforgeRemoveGridNode(CookbookBase):
    """Toolforge cookbook to reboot grid exec nodes"""

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
            "--node-hostnames",
            required=True,
            help="Short hostnames of nodes to remove",
            nargs="+",
            type=parser_type_list_hostnames,
        )
        parser.add_argument(
            "--master-node-fqdn",
            required=False,
            default=None,
            help=(
                "Name of the grid master node, will use <project>-sgegrid-master.<project>.eqiad1.wikimedia.cloud by "
                "default."
            ),
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, ToolforgeRemoveGridNodeRunner,)(
            node_hostnames=args.node_hostnames,
            master_node_fqdn=args.master_node_fqdn
            or f"{args.project}-sgegrid-master.{args.project}.eqiad1.wikimedia.cloud",
            spicerack=self.spicerack,
        )


class ToolforgeRemoveGridNodeRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeRemoveGridNode"""

    def __init__(
        self,
        common_opts: CommonOpts,
        node_hostnames: list[str],
        master_node_fqdn: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.node_hostnames = node_hostnames
        self.master_node_fqdn = master_node_fqdn
        super().__init__(spicerack=spicerack)
        self.sallogger = SALLogger(
            project=common_opts.project,
            task_id=common_opts.task_id,
            dry_run=common_opts.no_dologmsg,
        )

    def run(self) -> int | None:
        """Main entry point"""
        openstack_api = OpenstackAPI(
            remote=self.spicerack.remote(), cluster_name=OpenstackClusterName.EQIAD1, project=self.common_opts.project
        )
        grid_controller = GridController(remote=self.spicerack.remote(), master_node_fqdn=self.master_node_fqdn)

        for node_name in self.node_hostnames:
            node_fqdn = f"{node_name}.{self.common_opts.project}.eqiad1.wikimedia.cloud"

            if not openstack_api.server_exists(node_name, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT):
                LOGGER.warning("node %s is not a VM in project %s", node_fqdn, self.common_opts.project)
                return 1

            self.sallogger.log(f"removing grid node {node_fqdn}")

            LOGGER.info("Depooling the node from the grid")
            try:
                grid_controller.depool_node(host_fqdn=node_fqdn)
            except GridNodeNotFound:
                LOGGER.warning("node %s not found in the %s grid", node_fqdn, self.common_opts.project)

            LOGGER.info("Deleting the instance")
            remove_instance_cookbook = RemoveInstance(spicerack=self.spicerack)
            remove_instance_cookbook.get_runner(
                args=remove_instance_cookbook.argument_parser().parse_args(
                    [
                        "--server-name",
                        node_name,
                        "--no-dologmsg",  # not interested in the inner SAL entry
                        "--revoke-puppet-certs",  # so it will also be removed from puppetdb
                    ]
                    + self.common_opts.to_cli_args(),
                ),
            ).run()

            LOGGER.info("Reconfiguring the grid")
            # HACK: run the reconfigurator a few times, just to make sure it gets absolutely everything
            for _ in range(2):
                grid_controller.reconfigure(is_tools_project=self.common_opts.project == "tools")

        return 0
