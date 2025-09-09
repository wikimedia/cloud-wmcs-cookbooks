r"""WMCS Cloud VPS - Migrate a floating IP from one instance to another

Usage example:
  cookbook wmcs.vps.migrate_floating_ip \
    --cluster-name codfw1dev \
    --project project1 \
    --floating-ip 192.0.2.1 \
    --destination project1-server2
"""

from __future__ import annotations

import argparse
import logging
from ipaddress import IPv4Address

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class MigrateFloatingIp(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="OpenStack cluster where the project is located.",
        )
        parser.add_argument(
            "--floating-ip",
            required=True,
            type=IPv4Address,
            help="The IP address to move",
        )
        parser.add_argument(
            "--destination",
            required=True,
            help="The server to attach the floating IP to",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(self.spicerack, args, MigrateFloatingIpRunner)(
            cluster_name=args.cluster_name,
            floating_ip=args.floating_ip,
            destination=args.destination,
            spicerack=self.spicerack,
        )


class MigrateFloatingIpRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: OpenstackClusterName,
        floating_ip: IPv4Address,
        destination: str,
        spicerack: Spicerack,
    ):

        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=cluster_name,
            project=common_opts.project,
        )

        self.cluster_name: OpenstackClusterName = cluster_name
        self.floating_ip = floating_ip
        self.destination = destination
        self.common_opts: CommonOpts = common_opts
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        return f"for address {self.floating_ip} to server '{self.destination}'"

    def run(self) -> None:

        ip = self.openstack_api.floating_ip_show(self.floating_ip)
        if not ip:
            raise RuntimeError(f"Floating IP address {ip} not found")

        if ip.port_id:
            # Detach
            port = self.openstack_api.port_show(ip.port_id)
            if port.device_owner != "compute:nova" or port.device_id is None:
                raise RuntimeError(f"Floating IP address {ip} is attached to non-device port: {port}")

            LOGGER.info("Detaching floating IP %s from previous server %s", ip.floating_ip_address, port.device_id)
            self.openstack_api.server_remove_floating_ip(port.device_id, ip.floating_ip_address)

        LOGGER.info("Attaching floating IP %s to server %s", ip.floating_ip_address, self.destination)
        self.openstack_api.server_add_floating_ip(self.destination, ip.floating_ip_address)
