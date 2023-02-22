r"""WMCS Toolforge - tests - verify proper operations

Usage example:
    cookbook wmcs.toolforge.tests \
        --project toolsbeta \
        --bastion-hostname toolsbeta-sgebastion-05
"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import (
    CmdChecklist,
    CommonOpts,
    CuminParams,
    WMCSCookbookRunnerBase,
    add_common_opts,
    parser_type_str_hostname,
    with_common_opts,
)

LOGGER = logging.getLogger(__name__)


class ToolforgeTests(CookbookBase):
    """Toolforge cookbook to run the automated testsuite"""

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
            "--bastion-hostname",
            required=True,
            help=("Toolforge bastion hostname."),
            type=parser_type_str_hostname,
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, ToolforgeTestsRunner,)(
            bastion_hostname=args.bastion_hostname,
            spicerack=self.spicerack,
        )


class ToolforgeTestsRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeTests"""

    def __init__(
        self,
        common_opts: CommonOpts,
        bastion_hostname: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.bastion_hostname = bastion_hostname
        super().__init__(spicerack=spicerack)

    def run(self) -> int | None:
        """Main entry point"""
        fqdn = f"{self.bastion_hostname}.{self.common_opts.project}.eqiad1.wikimedia.cloud"
        bastion = self.spicerack.remote().query(f"D{{{fqdn}}}", use_sudo=True)

        checklist = CmdChecklist(
            name="Toolforge automated tests",
            remote_hosts=bastion,
            config_file="/etc/toolforge/automated-toolforge-tests.yaml",
        )
        results = checklist.run(cumin_params=CuminParams(print_progress_bars=False))
        return checklist.evaluate(results)
