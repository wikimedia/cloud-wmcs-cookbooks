r"""WMCS Toolforge - Remove an instance from a project.

Usage example:
    cookbook wmcs.vps.remove_instance \
        --project toolsbeta \
        --server-name toolsbeta-k8s-test-etcd-08

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from spicerack.puppet import PuppetMaster
from spicerack.remote import RemoteHosts

from wmcs_libs.common import (
    CommonOpts,
    CuminParams,
    WMCSCookbookRunnerBase,
    add_common_opts,
    run_one_raw,
    with_common_opts,
)
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class RemoveInstance(CookbookBase):
    """WMCS VPS cookbook to stop an instance."""

    title = __doc__

    def argument_parser(self) -> argparse.ArgumentParser:
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--revoke-puppet-certs",
            action="store_true",
            help="If set, the Puppet certificates of this server will be revoked on a custom Puppetmaster",
        )
        parser.add_argument(
            "--server-name",
            required=True,
            help="Name of the server to remove (without domain, ex. toolsbeta-test-k8s-etcd-9).",
        )
        parser.add_argument(
            "--already-off",
            action="store_true",
            help="Pass this if the server is turned off already, will skip some steps.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, RemoveInstanceRunner,)(
            name_to_remove=args.server_name,
            revoke_puppet_certs=args.revoke_puppet_certs,
            already_off=args.already_off,
            spicerack=self.spicerack,
        )


class RemoveInstanceRunner(WMCSCookbookRunnerBase):
    """Runner for RemoveInstance."""

    def __init__(
        self,
        common_opts: CommonOpts,
        name_to_remove: str,
        revoke_puppet_certs: bool,
        already_off: bool,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=OpenstackClusterName.EQIAD1,
            project=self.common_opts.project,
        )

        self.name_to_remove = name_to_remove
        self.revoke_puppet_certs = revoke_puppet_certs
        self.already_off = already_off
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for instance {self.name_to_remove}"

    def _guess_puppet_cert_hostname(self, remote: RemoteHosts | None, node_fqdn: str) -> str:
        if not remote:
            return node_fqdn

        try:
            # for legacy VMs in .eqiad.wmflabs
            result = run_one_raw(
                command=["hostname", "-f"],
                node=remote,
                cumin_params=CuminParams(print_output=False, print_progress_bars=False),
            )

            # idk why this is needed but it filters out 'mesg: ttyname failed: Inappropriate ioctl for device'
            return [
                line for line in result.splitlines() if line.endswith(".wikimedia.cloud") or line.endswith(".wmflabs")
            ][0]
        except IndexError:
            LOGGER.warning("Failed to query the hostname, falling back to the generated one")
            return node_fqdn

    def _guess_puppetmaster(self, remote: RemoteHosts | None, node_fqdn) -> str:
        if remote:
            puppet = self.spicerack.puppet(remote)
            puppet.disable(self.spicerack.admin_reason("host is being removed"))

            return puppet.get_ca_servers()[node_fqdn]

        domain = node_fqdn.split(".", 1)[-1]
        project = domain.split(".", 1)[0]
        # dummy guess
        return f"{project}-puppetmaster-1.{domain}"

    def run(self) -> None:
        """Main entry point"""
        if not self.openstack_api.server_exists(self.name_to_remove, cumin_params=CuminParams(print_output=False)):
            LOGGER.warning(
                "Unable to find server %s in project %s. Please review the project and server name.",
                self.name_to_remove,
                self.common_opts.project,
            )
            return

        if self.revoke_puppet_certs:
            node_fqdn = f"{self.name_to_remove}.{self.common_opts.project}.eqiad1.wikimedia.cloud"
            if self.already_off:
                remote = None
            else:
                remote = self.spicerack.remote().query(f"D{{{node_fqdn}}}", use_sudo=True)

            puppet_master_hostname = self._guess_puppetmaster(node_fqdn=node_fqdn, remote=remote)

            # if it's the central puppetmaster, this will be handled by wmf_sink
            if puppet_master_hostname not in ("puppet", "puppetmaster.cloudinfra.wmflabs.org"):
                puppet_master = PuppetMaster(
                    self.spicerack.remote().query(f"D{{{puppet_master_hostname}}}", use_sudo=True)
                )
                puppet_cert_hostname = self._guess_puppet_cert_hostname(remote, node_fqdn)
                puppet_master.delete(puppet_cert_hostname)

        self.openstack_api.server_delete(name_to_remove=self.name_to_remove)
