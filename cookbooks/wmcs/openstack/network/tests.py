"""WMCS openstack network tests - Run a network testsuite

Usage example:
  cookbook wmcs.openstack.network.tests --deployment codfw1dev
  cookbook wmcs.openstack.network.tests --deployment eqiad1

Documentation:
  https://wikitech.wikimedia.org/wiki/Portal:Cloud_VPS/Admin/Network/Tests

"""
import argparse
import logging
from typing import Optional

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase, CookbookRunnerBase

from cookbooks.wmcs import CmdChecklist
from cookbooks.wmcs.lib.openstack import Deployment, get_control_nodes

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

        parser.add_argument(
            "-d",
            "--deployment",
            help="openstack deployment where to run the tests",
            type=Deployment,
            choices=list(Deployment),
            default=Deployment.CODFW1DEV,
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> CookbookRunnerBase:
        """Get runner"""
        return NetworkTestRunner(
            deployment=args.deployment,
            spicerack=self.spicerack,
        )


class NetworkTestRunner(CookbookRunnerBase):
    """Runner for NetworkTests"""

    def __init__(self, deployment: Deployment, spicerack: Spicerack):
        """Init"""
        self.deployment: Deployment = deployment
        self.spicerack = spicerack

    def run(self) -> Optional[int]:
        """Main entry point"""
        # TODO: once we can run cumin with the puppetdb backend from our laptop
        # this ugly harcoding can be replaced to something like:
        # query = f"P{{O:wmcs::openstack::{self.deployment}::control}}"
        control_nodes = ",".join(get_control_nodes(self.deployment))
        query = f"D{{{control_nodes}}}"
        remote_hosts = self.spicerack.remote().query(query, use_sudo=True)

        # only interested in one control node
        for i in remote_hosts.split(len(remote_hosts)):
            control_node = i
            break

        checklist = CmdChecklist(
            name="Cloud VPS network tests", remote_hosts=control_node, config_file="/etc/networktests/networktests.yaml"
        )
        results = checklist.run(print_progress_bars=False)
        return checklist.evaluate(results)
