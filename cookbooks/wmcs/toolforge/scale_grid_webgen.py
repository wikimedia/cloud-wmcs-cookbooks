r"""WMCS Toolforge - scale the grid with a new grid webgen node.

Usage example:
    cookbook wmcs.toolforge.scale_grid_webgen \
        --project toolsbeta
"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.toolforge.grid.node.lib.create_join_pool import ToolforgeGridNodeCreateJoinPool
from cookbooks.wmcs.vps.create_instance_with_prefix import (
    InstanceCreationOpts,
    add_instance_creation_options,
    with_instance_creation_options,
)
from wmcs_libs.common import CommonOpts, DebianVersion, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.openstack.common import OpenstackServerGroupPolicy

LOGGER = logging.getLogger(__name__)


class ToolforgeScaleGridWebgen(CookbookBase):
    """WMCS Toolforge cookbook to scale up the grid with a new webgen node"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        add_common_opts(parser, project_default="toolsbeta")
        add_instance_creation_options(parser)
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
            "--debian-version",
            required=True,
            default=DebianVersion.BUSTER,
            choices=list(DebianVersion),
            type=DebianVersion.from_version_str,
            # TODO: Figure out the debian version from the image, or just not use it for the prefix
            help="Version of debian to use, as currently we are unable to get it from the image reliably.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack, args, with_instance_creation_options(args, ToolforgeScaleGridWebgenRunner)
        )(
            grid_master_fqdn=args.grid_master_fqdn
            or f"{args.project}-sgegrid-master.{args.project}.eqiad1.wikimedia.cloud",
            debian_version=args.debian_version,
            spicerack=self.spicerack,
        )


class ToolforgeScaleGridWebgenRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeScaleGridWebgen"""

    def __init__(
        self,
        common_opts: CommonOpts,
        grid_master_fqdn: str,
        spicerack: Spicerack,
        instance_creation_opts: InstanceCreationOpts,
        debian_version: DebianVersion = DebianVersion.BUSTER,
    ):
        """Init"""
        self.common_opts = common_opts
        self.grid_master_fqdn = grid_master_fqdn
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.debian_version = debian_version
        self.instance_creation_opts = instance_creation_opts

    def run(self) -> None:
        """Main entry point"""
        inner_args = (
            [
                "--security-group",
                "webserver",
                "--server-group",
                f"{self.common_opts.project}-sgegrid-webgen-nodes",
                "--server-group-policy",
                OpenstackServerGroupPolicy.SOFT_ANTI_AFFINITY.value,
                "--debian-version",
                self.debian_version.name.lower(),
                "--nodetype",
                "webgen",
            ]
            + self.common_opts.to_cli_args()
            + self.instance_creation_opts.to_cli_args()
        )

        create_node_cookbook = ToolforgeGridNodeCreateJoinPool(spicerack=self.spicerack)
        create_node_cookbook_arg_parser = create_node_cookbook.argument_parser()
        parsed_inner_args = create_node_cookbook_arg_parser.parse_args(inner_args)
        create_node_cookbook_runner = create_node_cookbook.get_runner(args=parsed_inner_args)
        create_node_cookbook_runner.run()
