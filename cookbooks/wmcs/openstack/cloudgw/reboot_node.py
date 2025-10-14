r"""WMCS Openstack - Reboot a cloudgw node .

Usage example:
    cookbook wmcs.openstack.cloudgw.reboot_node \
    --fqdn-to-reboot cloudgw1002.eqiad.wmnet

"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.openstack.network.tests import NetworkTests
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import get_gateway_nodes, get_node_cluster_name

LOGGER = logging.getLogger(__name__)


class RebootNode(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--fqdn-to-reboot",
            required=True,
            help="FQDN of the node to reboot.",
        )
        parser.add_argument(
            "--skip-checks",
            required=False,
            action="store_true",
            help="If passed, will not test the network before or after rebooting the node.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(
            self.spicerack,
            args,
            RebootNodeRunner,
        )(
            fqdn_to_reboot=args.fqdn_to_reboot,
            skip_checks=args.skip_checks,
            spicerack=self.spicerack,
        )


def check_network_ok(cluster_name: OpenstackClusterName, spicerack: Spicerack) -> None:
    """Run the network tests and check if they pass."""
    args = ["--cluster-name", str(cluster_name)]
    network_test_cookbook = NetworkTests(spicerack=spicerack)
    if network_test_cookbook.get_runner(args=network_test_cookbook.argument_parser().parse_args(args)).run() != 0:
        raise Exception("Network tests failed, see logs or run the cookbook for details.")


class RebootNodeRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        fqdn_to_reboot: str,
        skip_checks: bool,
        spicerack: Spicerack,
    ):

        self.common_opts = common_opts
        self.fqdn_to_reboot = fqdn_to_reboot
        self.skip_checks = skip_checks
        super().__init__(spicerack=spicerack, common_opts=common_opts)

        self.cluster_name = get_node_cluster_name(node=self.fqdn_to_reboot)

        known_cloudgws = get_gateway_nodes(self.cluster_name)
        if not known_cloudgws:
            raise Exception(f"No cloudgws found for cluster_name {self.cluster_name} :-S")

        if len(known_cloudgws) == 1 and not self.skip_checks:
            raise Exception(
                f"There's only one gateway node for the cluster_name {self.cluster_name} ({known_cloudgws}), and the "
                "network will go dow if rebooted, pass --skip-checks to ignore."
            )

        if self.fqdn_to_reboot not in known_cloudgws:
            raise Exception(
                f"Host {self.fqdn_to_reboot} is not part of the cloudgw for cluster_name {self.cluster_name}"
            )

        if not self.skip_checks:
            LOGGER.info("Checking the current state of the network...")
            check_network_ok(cluster_name=self.cluster_name, spicerack=self.spicerack)
            LOGGER.info("Network up and running!")

    @property
    def runtime_description(self) -> str:
        return f"for host {self.fqdn_to_reboot}"

    def run_with_proxy(self) -> None:

        node = self.spicerack.remote().query(f"D{{{self.fqdn_to_reboot}}}", use_sudo=True)
        am_hosts = self.spicerack.alertmanager_hosts(node.hosts)

        with am_hosts.downtimed(
            reason=self.spicerack.admin_reason(
                "Rebooting with wmcs.openstack.cloudgw.reboot_node", task_id=self.common_opts.task_id
            ),
            duration=timedelta(hours=1),
        ):
            reboot_time = datetime.utcnow()
            node.reboot()

            node.wait_reboot_since(since=reboot_time)
            LOGGER.info(
                "Rebooted node %s, waiting for cluster to stabilize...",
                self.fqdn_to_reboot,
            )

            if not self.skip_checks:
                LOGGER.info("Checking if the network is up and running")
                check_network_ok(cluster_name=self.cluster_name, spicerack=self.spicerack)
                LOGGER.info("Network up and running!")
