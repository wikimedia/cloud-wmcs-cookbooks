r"""WMCS Toolforge - Migrate a given NFS volume from one host to another

Usage example:
    cookbook wmcs.nfs.migrate_service \
        --from-id <old server id> \
        --to-id <new server id> \
        --project <project_id> \
        --force

the old and new hosts must already have been created using similar add_server
calls such that they have the same puppet/hiera config.
"""

from __future__ import annotations

import argparse
import logging
import socket
import time
from typing import Any, Optional, Union

from cumin.transports import Command
from spicerack import Spicerack
from spicerack.cookbook import CookbookBase
from spicerack.puppet import PuppetHosts
from wmflib.interactive import ask_confirmation

from wmcs_libs.common import (
    CommonOpts,
    WMCSCookbookRunnerBase,
    add_common_opts,
    get_ip_address_family,
    run_one_raw,
    with_common_opts,
)
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI
from wmcs_libs.openstack.enc import Enc

LOGGER = logging.getLogger(__name__)

OpenstackID = str
OpenstackName = str
# For some reason python 3.9 does not like using `|` for aliases
OpenstackIdentifier = Union[OpenstackID, OpenstackName]


def _quote(mystr: str) -> str:
    """Wraps the given string in single quotes."""
    return f"'{mystr}'"


class NFSServiceMigrateVolume(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):
        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument("--from-host-id", required=True, help="old service host ID")
        parser.add_argument("--to-host-id", required=True, help="new service host ID")
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "If set, do not try to stop existing services and unmount volume from old host. "
                "Useful when the oldhost is down."
            ),
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(self.spicerack, args, NFSServiceMigrateVolumeRunner)(
            from_id=args.from_host_id,
            to_id=args.to_host_id,
            force=args.force,
            spicerack=self.spicerack,
        )


class NFSServiceMigrateVolumeRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        from_id: OpenstackID,
        to_id: OpenstackID,
        force: bool,
        spicerack: Spicerack,
    ):

        self.from_id = from_id
        self.to_id = to_id
        self.project = common_opts.project
        self.force = force
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.openstack_api = OpenstackAPI(
            remote=self.spicerack.remote(), cluster_name=OpenstackClusterName.EQIAD1, project=self.project
        )
        self.from_server = self.openstack_api.server_from_id(self.from_id)
        self.to_server = self.openstack_api.server_from_id(self.to_id)

        self.from_name = self.from_server["name"]
        self.to_name = self.to_server["name"]

        self.from_fqdn = f"{self.from_name}.{self.project}.eqiad1.wikimedia.cloud"
        self.to_fqdn = f"{self.to_name}.{self.project}.eqiad1.wikimedia.cloud"

    def run(self) -> None:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements

        if not self.from_server["volumes_attached"] and self.force:
            LOGGER.warning("Source server has no volume attached, checking if target already has an attachment")
            volume_id = self.to_server["volumes_attached"][0]["id"]
        else:
            volume_id = self.from_server["volumes_attached"][0]["id"]
        volume = self.openstack_api.volume_from_id(volume_id)
        volume_name = volume["name"]

        from_node = self.spicerack.remote().query(f"D{{{self.from_fqdn}}}", use_sudo=True)
        to_node = self.spicerack.remote().query(f"D{{{self.to_fqdn}}}", use_sudo=True)

        # Verify that puppet/hiera config agrees between the two hosts
        enc = Enc(remote=self.spicerack.remote(), cluster_name=OpenstackClusterName.EQIAD1)

        from_enc_prefix = enc.prefix(self.project, self.from_fqdn)
        from_hiera = from_enc_prefix.get_current_hiera()
        from_roles = from_enc_prefix.get_current_roles()

        if "role::wmcs::nfs::standalone" not in from_roles:
            raise Exception(
                f"Server {self.from_fqdn} does not use role::wmcs::nfs::standalone "
                f"This cookbook only works on that role. Roles are {from_roles}"
            )

        if (
            "profile::wmcs::nfs::standalone::volumes" not in from_hiera
            or len(from_hiera["profile::wmcs::nfs::standalone::volumes"]) != 1
        ):
            raise Exception(
                f"Server {self.from_fqdn} must have exactly one value set for profile::wmcs::nfs::standalone::volumes."
            )

        mount_name = from_hiera["profile::wmcs::nfs::standalone::volumes"][0]

        to_enc_prefix = enc.prefix(self.project, self.to_fqdn)
        to_hiera = to_enc_prefix.get_current_hiera()
        to_roles = to_enc_prefix.get_current_roles()

        if "role::wmcs::nfs::standalone" not in to_roles:
            raise Exception(
                f"Server {self.to_fqdn} does not use role::wmcs::nfs::standalone "
                f"This cookbook only works on that role. Roles are {to_roles}"
            )

        if (
            "profile::wmcs::nfs::standalone::volumes" not in to_hiera
            or len(to_hiera["profile::wmcs::nfs::standalone::volumes"]) != 1
            or to_hiera["profile::wmcs::nfs::standalone::volumes"][0] != mount_name
        ):
            raise Exception(
                f"Server {self.to_fqdn} must have profile::wmcs::nfs::standalone::volumes: ['{mount_name}']"
            )

        if (
            "profile::wmcs::nfs::standalone::cinder_attached" in to_hiera
            and to_hiera["profile::wmcs::nfs::standalone::cinder_attached"]
            and not self.force
        ):
            raise Exception(
                f"Server {self.to_fqdn} already seems to have a volume attached "
                "(profile::wmcs::nfs::standalone::cinder_attached=True)"
            )

        # locate the "from" service IP
        service_zone = f"svc.{self.project}.eqiad1.wikimedia.cloud."
        service_fqdn = f"{volume_name}.{service_zone}"
        service_ip = run_one_raw(node=to_node, command=["dig", "+short", service_fqdn], last_line_only=True).strip()
        if not service_ip:
            raise Exception(f"Unable to resolve service ip for service name {service_fqdn}")
        service_ip_port = self.openstack_api.port_get_by_ip(service_ip)
        if not service_ip_port:
            raise Exception(f"Did not find port for service IP {service_ip} ({service_fqdn})")

        to_ip = run_one_raw(node=to_node, command=["dig", "+short", self.to_fqdn], last_line_only=True).strip()
        to_port = self.openstack_api.port_get_by_ip(to_ip)[0]
        from_ip = run_one_raw(node=to_node, command=["dig", "+short", self.from_fqdn], last_line_only=True).strip()
        from_port = self.openstack_api.port_get_by_ip(from_ip)[0]

        # Detect if "to" and "from" hosts are in different networks.
        # If so, fetch the "to" service IP and flag the change as DNS
        to_network = self.openstack_api.port_show(to_port.port_id).network_id
        from_network = self.openstack_api.port_show(from_port.port_id).network_id

        service_ip_new = None
        service_ip_dns = None
        network_changed = False
        if to_network != from_network:
            service_ip_new = self._fetch_service_ip(volume_name, to_network)
            service_ip_dns = DNSFlip(self.openstack_api, service_zone, service_fqdn)
            service_ip_dns.prepare()
            network_changed = True

        # See if wmcs-prepare-cinder-volume has already been run on the target host
        volume_path = f"/srv/{mount_name}"
        volume_prepared = False

        fstab_content = run_one_raw(node=to_node, command=["cat", "/etc/fstab"])

        if volume_path in fstab_content:
            volume_prepared = True

        # That's all our checks. No start actually doing things.

        # Disable puppet to avoid races
        to_puppet = PuppetHosts(to_node)
        from_puppet = PuppetHosts(from_node)

        reason = self.spicerack.admin_reason(f"migrating nfs service from {self.from_fqdn} to {self.to_fqdn}")
        to_puppet.disable(reason)

        if not self.force:
            from_puppet.disable(reason)
            run_one_raw(node=from_node, command=["systemctl", "stop", "nfs-server.service"])
            run_one_raw(node=from_node, command=["umount", volume_path])

        try:
            self.openstack_api.volume_detach(self.from_id, volume_id)
            self.openstack_api.volume_attach(self.to_id, volume_id)
        except Exception as error:  # pylint: disable=broad-except
            if not self.force:
                LOGGER.warning("Ignoring exception due to --force: %s", error)
                raise error

        if volume_prepared:
            # Don't fail if it's already mounted.
            run_one_raw(command=Command(command=f"mount {volume_path}", ok_codes=[]), node=to_node)
        else:
            run_one_raw(
                node=to_node,
                command=[
                    "wmcs-prepare-cinder-volume",
                    "--device",
                    "sdb",
                    "--options",
                    "'rw,nofail,x-systemd.device-timeout=2s,noatime,data=ordered'",
                    "--mountpoint",
                    volume_path,
                    "--force",
                ],
            )

        # Tell puppet that cinder is detached on the old host and attached on the new one
        from_hiera["profile::wmcs::nfs::standalone::cinder_attached"] = False
        from_enc_prefix.set_hiera_values(from_hiera)

        to_hiera["profile::wmcs::nfs::standalone::cinder_attached"] = True
        to_enc_prefix.set_hiera_values(to_hiera)

        # If we are migrating between hosts in different networks, do DNS change and wait for propagation
        if network_changed:
            if service_ip_new is None or service_ip_dns is None:
                raise ValueError("New service IP or DNS name not found.")
            service_ip_dns.flip_to(service_ip_new)
            service_ip_dns.wait(to_node)
        # Otherwise, it is sufficient to move the service IP between ports with Neutron
        else:
            try:
                self.openstack_api.detach_service_ip(service_ip, from_port.mac_address, from_port.port_id)
                self.openstack_api.attach_service_ip(service_ip, to_port.port_id)
            except Exception as error:  # pylint: disable=broad-except
                if not self.force:
                    LOGGER.warning("Ignoring exception due to --force: %s", error)
                    raise error

        # Apply all pending puppet changes
        if not self.force:
            from_puppet.enable(reason)
            from_puppet.run()

        to_puppet.enable(reason)
        to_puppet.run()
        run_one_raw(node=to_node, command=["systemctl", "start", "nfs-server.service"])

    def _fetch_service_ip(self, name: str, network: OpenstackID) -> Optional[str]:
        """Return the service IP 'name' in network 'network', or None."""
        service_ips = self.openstack_api.get_service_ips(name, network)
        if len(service_ips) != 1:
            raise ValueError(f"Invalid service IPs found: {service_ips!r}")

        for entry in service_ips[0]["Fixed IP Addresses"]:
            if get_ip_address_family(entry["ip_address"]) == socket.AF_INET:
                return entry["ip_address"]

        return None


class DNSFlip:
    """Represents an Openstack / Designate based DNS flip operation.

    OpenTofu-managed records will not be flipped, instead the user will be asked
    to apply changes themselves.
    """

    WAIT_SLEEP_SECONDS = 15
    TOFU_CONFIRM_MESSAGE = """
    The record {self.fqdn} and zone {self.zone_name} are managed by OpenTofu.
    Make sure you update this project's tofu-provisioning git repository and
    apply your changes to flip DNS.
    Only continue once the OpenTofu changes have been applied. Ok to continue?
    """

    def __init__(self, openstack_api: OpenstackAPI, zone_name: str, fqdn: str, record_type: str = "A"):
        self.api = openstack_api
        self.zone_name = zone_name
        self.fqdn = fqdn
        self.record_type = record_type

        self.zone = None
        self.record = None
        self.record_data = None
        self.record_details = None
        self.tofu_managed = False
        self.to_address = ""

    def prepare(self):
        """Gather data from Openstack in preparation for the flip."""
        self.zone = self.api.zone_get(self.zone_name)[0]
        self.record = self.api.recordset_get(self.zone["id"], self.record_type, self.fqdn)[0]
        self.record_details = self.api.recordset_show(self.zone["id"], self.record["id"])
        if self.record_details["description"] is not None:
            self.tofu_managed = "managed by tofu" in self.record_details["description"]

    def flip_to(self, address: str):
        """Flip the service record to the specified address."""
        if self.zone is None or self.record is None:
            raise ValueError("Zone or record not found.")

        if self.tofu_managed:
            ask_confirmation(self.TOFU_CONFIRM_MESSAGE.format(**vars(self)))
            return

        self.to_address = address
        self.api.recordset_delete(self.zone["id"], self.record["id"])
        self.api.recordset_create(self.zone["id"], self.record_type, self.fqdn, self.to_address)

    def wait(self, node: Any, timeout: int = 120) -> bool:
        """Wait on 'node' for the DNS flip to be propagated, up to 'timeout' seconds.
        Returns True if the flip was successful, False otherwise.
        """
        if not self.to_address:
            raise ValueError("Operation not prepared. Unable to find destination IP address.")

        deadline = time.time() + timeout

        while time.time() < deadline:
            current_address = run_one_raw(node=node, command=["dig", "+short", self.fqdn], last_line_only=True).strip()
            if current_address == self.to_address:
                return True
            time.sleep(self.WAIT_SLEEP_SECONDS)

        return False
