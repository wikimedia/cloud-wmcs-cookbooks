r"""WMCS openstack - Log a SAL message

Usage example: wmcs.do_log_msg \
    --msg "I just changed some config in cloudvirt1020" \
    --task-id T424242

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts

LOGGER = logging.getLogger(__name__)


class Dologmsg(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--msg",
            required=True,
            help="Message to log.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(
            self.spicerack,
            args,
            DologmsgRunner,
        )(
            msg=args.msg,
            spicerack=self.spicerack,
        )


class DologmsgRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        msg: str,
        spicerack: Spicerack,
    ):

        self.msg = msg
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run(self) -> None:

        self.spicerack.sal_logger.info("%s", self.msg)
