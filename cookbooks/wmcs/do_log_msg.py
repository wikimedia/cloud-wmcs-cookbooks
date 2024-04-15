r"""WMCS openstack - Log a SAL message

Usage example: wmcs.do_log_msg \
    --msg "I just changed some config in cloudvirt1020" \
    --task-id T424242

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts

LOGGER = logging.getLogger(__name__)


class Dologmsg(CookbookBase):
    """WMCS cookbook to log a SAL message."""

    __title__ = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--msg",
            required=True,
            help="Message to log.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            DologmsgRunner,
        )(
            msg=args.msg,
            spicerack=self.spicerack,
        )


class DologmsgRunner(WMCSCookbookRunnerBase):
    """Runner for Dologmsg."""

    def __init__(
        self,
        common_opts: CommonOpts,
        msg: str,
        spicerack: Spicerack,
    ):
        """Init."""
        self.msg = msg
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run(self) -> None:
        """Main entry point."""
        self.spicerack.sal_logger.info("%s", self.msg)
