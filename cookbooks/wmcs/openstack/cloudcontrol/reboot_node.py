r"""WMCS Openstack - Reboot a cloudcontrol node .

Usage example:
    cookbook wmcs.openstack.cloudcontrol.reboot_node \
    --fqdn-to-reboot cloudcontrol1005.eqiad.wmnet

"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.alerts import downtime_host, uptime_host
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.openstack.common import get_control_nodes, get_node_cluster_name

LOGGER = logging.getLogger(__name__)


class RebootNode(CookbookBase):
    """WMCS Openstack cookbook to reboot a single cloudcontrols, handling failover."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--fqdn-to-reboot",
            required=True,
            help="FQDN of the node to reboot.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, RebootNodeRunner,)(
            fqdn_to_reboot=args.fqdn_to_reboot,
            spicerack=self.spicerack,
        )


class RebootNodeRunner(WMCSCookbookRunnerBase):
    """Runner for RebootNode"""

    def __init__(
        self,
        common_opts: CommonOpts,
        fqdn_to_reboot: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.fqdn_to_reboot = fqdn_to_reboot
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)

        self.cluster_name = get_node_cluster_name(self.fqdn_to_reboot)

        known_cloudcontrols = get_control_nodes(cluster_name=self.cluster_name)
        if not known_cloudcontrols:
            raise Exception(f"No cloudcontrols found for cluster_name {self.cluster_name} :-S")

        if self.fqdn_to_reboot not in known_cloudcontrols:
            raise Exception(
                f"Host {self.fqdn_to_reboot} is not part of the cloudcontrol for cluster_name {self.cluster_name}"
            )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        self.sallogger.log(f"Rebooting cloudcontrol host {self.fqdn_to_reboot}")
        node = self.spicerack.remote().query(f"D{{{self.fqdn_to_reboot}}}", use_sudo=True)
        host_name = self.fqdn_to_reboot.split(".", 1)[0]
        host_silence_id = downtime_host(
            spicerack=self.spicerack,
            host_name=host_name,
            comment="Rebooting with wmcs.openstack.cloudcontrol.reboot_node",
            task_id=self.common_opts.task_id,
        )

        reboot_time = datetime.utcnow()
        node.reboot()

        node.wait_reboot_since(since=reboot_time)

        uptime_host(spicerack=self.spicerack, host_name=host_name, silence_id=host_silence_id)
        LOGGER.info("Silences removed.")

        self.sallogger.log(f"Rebooted cloudcontrol host {self.fqdn_to_reboot}")
