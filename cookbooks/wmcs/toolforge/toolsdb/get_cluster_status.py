r"""WMCS Toolforge - ToolsDB - get cluster status

Usage example:
    cookbook wmcs.toolforge.toolsdb.get_cluster_status \
        --project tools
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.toolsdb import ToolforgeToolsDBClusterName
from wmcs_libs.toolsdb import ToolsDBController

LOGGER = logging.getLogger(__name__)


class ToolsDBGetClusterStatus(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        parser.add_argument(
            "--cluster-name",
            required=False,
            choices=list(ToolforgeToolsDBClusterName),
            type=ToolforgeToolsDBClusterName,
            default=ToolforgeToolsDBClusterName.TOOLS,
            help="cluster to work on",
        )
        add_common_opts(parser)

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        # This is a read-only cookbook, we don't want to log to SAL
        args.no_dologmsg = True
        return with_common_opts(self.spicerack, args, ToolsDBGetClusterStatusRunner)(
            spicerack=self.spicerack,
            cluster_name=args.cluster_name,
        )


class ToolsDBGetClusterStatusRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeToolsDBClusterName,
        spicerack: Spicerack,
    ):

        self.project = common_opts.project
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.toolsdb_controller = ToolsDBController(remote=self.spicerack.remote(), cluster_name=cluster_name)

    def run(self) -> None:

        cluster_status = self.toolsdb_controller.get_cluster_status()
        print(json.dumps(asdict(cluster_status), indent=4))
