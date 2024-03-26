r"""WMCS Toolforge - Add a new nfs server on a VM

Usage example:
    cookbook wmcs.nfs.add_server \
        --project cloudinfra-nfs \
        --create-storage-volume-size 200 \
        --prefix toolsbeta \
        toolsbeta-home toolsbeta-project

"""
# pylint: disable=too-many-arguments
from __future__ import annotations

import argparse
import logging
from typing import Any

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.vps.create_instance_with_prefix import (
    CreateInstanceWithPrefix,
    InstanceCreationOpts,
    add_instance_creation_options,
    with_instance_creation_options,
)
from wmcs_libs.common import (
    CommonOpts,
    SALLogger,
    WMCSCookbookRunnerBase,
    add_common_opts,
    run_one_raw,
    with_common_opts,
)
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI, OpenstackID
from wmcs_libs.openstack.enc import Enc

LOGGER = logging.getLogger(__name__)


class NFSAddServer(CookbookBase):
    """WMCS Toolforge cookbook to add a new nfs server"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
        )
        add_common_opts(parser, project_default="cloudinfra-nfs")
        parser.add_argument(
            "--service-ip",
            action="store_true",
            help="If set, a service IP and fqdn will be created and attached to the new host.",
        )
        parser.add_argument(
            "--create-storage-volume-size",
            type=int,
            required=False,
            default=0,
            help="Size for created storage volume. If unset, no volume will be created; "
            "an existing volume can be attached later.",
        )
        add_instance_creation_options(parser)
        parser.add_argument("volume", help=("nfs volume to be provided and managed by this server"))

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        runner = with_common_opts(self.spicerack, args, NFSAddServerRunner)
        runner = with_instance_creation_options(args, runner)
        return runner(
            prefix=args.prefix,
            volume=args.volume,
            service_ip=args.service_ip,
            create_storage_volume_size=args.create_storage_volume_size,
            spicerack=self.spicerack,
        )


class NFSAddServerRunner(WMCSCookbookRunnerBase):
    """Runner for NFSAddServer"""

    def __init__(
        self,
        prefix: str,
        service_ip: bool,
        volume: str,
        create_storage_volume_size: int,
        spicerack: Spicerack,
        instance_creation_opts: InstanceCreationOpts,
        common_opts: CommonOpts,
    ):
        """Init"""
        self.create_storage_volume_size = create_storage_volume_size
        self.volume = volume
        self.project = common_opts.project
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.prefix = prefix
        self.service_ip = service_ip
        self.instance_creation_opts = instance_creation_opts
        if self.instance_creation_opts.network is None:
            raise Exception("Missing network please provide one")

        self.network = self.instance_creation_opts.network
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)

    def run(self) -> None:
        """Main entry point"""
        prefix = self.prefix if self.prefix is not None else f"{self.volume}"

        start_args = [
            "--project",
            self.project,
            "--prefix",
            prefix,
            "--security-group",
            "nfs",
            "--sign-puppet-certs",
        ] + self.instance_creation_opts.to_cli_args()

        create_instance_cookbook = CreateInstanceWithPrefix(spicerack=self.spicerack)
        new_server = create_instance_cookbook.get_runner(
            args=create_instance_cookbook.argument_parser().parse_args(start_args)
        ).create_instance()

        new_node = self.spicerack.remote().query(f"D{{{new_server.server_fqdn}}}", use_sudo=True)
        openstack_api = OpenstackAPI(
            remote=self.spicerack.remote(), cluster_name=OpenstackClusterName.EQIAD1, project=self.project
        )

        if self.create_storage_volume_size > 0:
            new_volume = openstack_api.volume_create(OpenstackID(self.prefix), self.create_storage_volume_size)

            openstack_api.volume_attach(new_server.server_id, new_volume)
        # TODO: check for the volume existing if not creating one

        enc = Enc(remote=self.spicerack.remote(), cluster_name=openstack_api.cluster_name)
        enc_prefix = enc.prefix(self.project, new_server.server_fqdn)

        # We assume new hosts don't have any host-specific hiera (as the methods
        #   to get that data crash if the prefix does not exist.)
        # TODO: fix those methods crashing
        current_hiera: dict[str, Any] = {}
        current_roles = []

        # Add nfs volume
        current_hiera["profile::wmcs::nfs::standalone::volumes"] = [self.volume]
        if self.create_storage_volume_size > 0:
            current_hiera["profile::wmcs::nfs::standalone::cinder_attached"] = True
        else:
            current_hiera["profile::wmcs::nfs::standalone::cinder_attached"] = False
        current_hiera["mount_nfs"] = False

        enc_prefix.replace_hiera(current_hiera)

        # Add nfs server puppet role
        current_roles.append("role::wmcs::nfs::standalone")
        enc_prefix.replace_roles(current_roles)

        if self.create_storage_volume_size > 0:
            run_one_raw(
                command=[
                    "wmcs-prepare-cinder-volume",
                    "--device",
                    "sdb",
                    "--options",
                    "'rw,nofail,x-systemd.device-timeout=2s,noatime,data=ordered'",
                    "--mountpoint",
                    f"'/srv/{self.volume}'",
                    "--force",
                ],
                node=new_node,
            )

        if self.service_ip:
            host_port = openstack_api.port_get_for_server(new_server.server_id)[0]

            service_ip_response = openstack_api.create_service_ip(self.volume, self.network)
            service_ip = service_ip_response["fixed_ips"][0]["ip_address"]

            logging.warning("The new service_ip is %s", service_ip)
            openstack_api.attach_service_ip(service_ip, host_port.port_id)

            zone_record = openstack_api.zone_get(f"svc.{self.project}.eqiad1.wikimedia.cloud.")
            openstack_api.recordset_create(
                zone_record[0]["id"], "A", f"{self.prefix}.svc.{self.project}.eqiad1.wikimedia.cloud.", service_ip
            )

        # Apply all pending changes
        run_one_raw(node=new_node, command=["/usr/local/sbin/run-puppet-agent"])
        self.sallogger.log(f"created NFS server {new_server.server_fqdn}")
