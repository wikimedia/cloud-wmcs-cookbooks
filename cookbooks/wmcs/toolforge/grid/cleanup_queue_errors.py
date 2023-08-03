r"""WMCS Toolforge - grid - cleanup queue errors

Usage example:
    cookbook wmcs.toolforge.grid.cleanup_queue_errors \
        --project toolsbeta \
        --master-hostname toolsbeta-sgegrid-master
"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import (
    CommonOpts,
    SALLogger,
    WMCSCookbookRunnerBase,
    add_common_opts,
    parser_type_str_hostname,
    with_common_opts,
)
from wmcs_libs.grid import GridController

LOGGER = logging.getLogger(__name__)


class ToolforgeGridCleanupQ(CookbookBase):
    """Toolforge cookbook to cleanup queue errors"""

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
            "--master-hostname",
            required=False,
            type=parser_type_str_hostname,
            help="The hostname of the grid master node. Default is '<project>-sgegrid-master'",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, ToolforgeGridCleanupQRunner,)(
            master_hostname=args.master_hostname,
            spicerack=self.spicerack,
        )


class ToolforgeGridCleanupQRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeGridCleanupQ"""

    def __init__(
        self,
        common_opts: CommonOpts,
        master_hostname: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.master_hostname = master_hostname
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )

        if not self.master_hostname:
            self.master_hostname = f"{self.common_opts.project}-sgegrid-master"

    def run(self) -> None:
        """Main entry point"""
        master_fqdn = f"{self.master_hostname}.{self.common_opts.project}.eqiad1.wikimedia.cloud"
        LOGGER.info("INFO: using master node FQDN %s", master_fqdn)
        grid_controller = GridController(self.spicerack.remote(), master_fqdn)

        grid_controller.cleanup_queue_errors()
        self.sallogger.log(message=f"cleaned up grid queue errors on {self.master_hostname}")
