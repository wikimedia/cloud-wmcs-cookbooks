r"""WMCS openstack network tests - Run a network testsuite

Usage example:
  cookbook wmcs.openstack.network.tests --cluster_name codfw1dev
  cookbook wmcs.openstack.network.tests --cluster_name eqiad1

Documentation:
  https://wikitech.wikimedia.org/wiki/Portal:Cloud_VPS/Admin/Network/Tests

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import (
    CmdChecklist,
    CommonOpts,
    CuminParams,
    WMCSCookbookRunnerBase,
    add_common_opts,
    with_common_opts,
)
from wmcs_libs.inventory import OpenstackClusterName
from wmcs_libs.openstack.common import get_control_nodes

LOGGER = logging.getLogger(__name__)


class NetworkTests(CookbookBase):
    """WMCS openstack cookbook to run automated network tests/checks."""

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
            "--cluster-name",
            help="openstack cluster_name where to run the tests",
            type=OpenstackClusterName,
            choices=list(OpenstackClusterName),
            default=OpenstackClusterName.CODFW1DEV,
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, NetworkTestRunner)(
            cluster_name=args.cluster_name,
            spicerack=self.spicerack,
        )


class NetworkTestRunner(WMCSCookbookRunnerBase):
    """Runner for NetworkTests"""

    def __init__(self, common_opts: CommonOpts, cluster_name: OpenstackClusterName, spicerack: Spicerack):
        """Init"""
        self.cluster_name: OpenstackClusterName = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run(self) -> int | None:
        """Main entry point"""
        control_node = get_control_nodes(self.cluster_name)[0]
        query = f"D{{{control_node}}}"
        remote_host = self.spicerack.remote().query(query, use_sudo=True)

        checklist = CmdChecklist(
            name="Cloud VPS network tests", remote_hosts=remote_host, config_file="/etc/networktests/networktests.yaml"
        )
        results = checklist.run(cumin_params=CuminParams(print_progress_bars=False))
        return checklist.evaluate(results)
