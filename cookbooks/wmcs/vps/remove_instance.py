r"""WMCS Toolforge - Remove an instance from a project.

Usage example:
    cookbook wmcs.vps.remove_instance \
        --project toolsbeta \
        --server-name toolsbeta-k8s-test-etcd-08

"""

from __future__ import annotations

import argparse
import logging
from datetime import timedelta

from spicerack import Spicerack, SpicerackError
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from spicerack.puppet import PuppetHosts, PuppetServer
from spicerack.remote import RemoteExecutionError, RemoteHosts

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
from wmcs_libs.openstack.enc import Enc

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
            "--cluster-name",
            required=False,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            default=OpenstackClusterName.EQIAD1,
            help="Openstack cluster_name where the VM is hosted.",
        )
        parser.add_argument(
            "--server-name",
            required=True,
            help="Name of the server to remove (without domain, ex. toolsbeta-test-k8s-etcd-9).",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            RemoveInstanceRunner,
        )(
            cluster_name=args.cluster_name,
            name_to_remove=args.server_name,
            spicerack=self.spicerack,
        )


class RemoveInstanceRunner(WMCSCookbookRunnerBase):
    """Runner for RemoveInstance."""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: OpenstackClusterName,
        name_to_remove: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=cluster_name,
            project=self.common_opts.project,
        )

        self.cluster_name = cluster_name
        self.name_to_remove = name_to_remove
        super().__init__(spicerack=spicerack, common_opts=common_opts)

        self.admin_reason = self.spicerack.admin_reason("host is being removed", self.common_opts.task_id)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for instance {self.name_to_remove}"

    def _guess_puppet_cert_hostname(self, remote: RemoteHosts | None, node_fqdn: str) -> str:
        if remote:
            try:
                # for legacy VMs in .eqiad.wmflabs
                result = run_one_raw(
                    command=["hostname", "-f"],
                    node=remote,
                    cumin_params=CuminParams(print_output=False, print_progress_bars=False),
                )

                # idk why this is needed but it filters out 'mesg: ttyname failed: Inappropriate ioctl for device'
                return [
                    line
                    for line in result.splitlines()
                    if line.endswith(".wikimedia.cloud") or line.endswith(".wmflabs")
                ][0]
            except IndexError:
                LOGGER.warning("Failed to query the hostname, falling back to the generated one")
        return node_fqdn

    def _find_puppetserver(self, puppet_hosts: PuppetHosts | None, node_fqdn: str) -> str:
        if puppet_hosts:
            try:
                return puppet_hosts.get_ca_servers()[node_fqdn]
            except RemoteExecutionError:
                # Ignore, VM is probably broken or something. Just fall back to the hiera lookup below.
                LOGGER.warning("Failed to query the current Puppet server, falling back to Hiera")
        enc = Enc(remote=self.spicerack.remote(), cluster_name=self.cluster_name)
        return enc.node_config(self.common_opts.project, self.name_to_remove).hiera.get("puppetmaster", "puppet")

    def _downtime(self) -> None:
        """Set a short downtime to prevent InstanceDown alerts before Prometheus notices the server was removed."""
        # TODO: eventually we should have an AlertmanagerHosts-style interface that supports Cloud VPS instances.
        # For now this is quite manual and not very DRY.

        try:
            alertmanager = self.spicerack.alertmanager(instance_name=f"metricsinfra-{self.cluster_name.value}")
        except SpicerackError as e:
            # Most likely this means we're running in codfw1dev, or on a local setup with no metricsinfra configuration.
            LOGGER.info("Not downtiming alerts because Alertmanager is not available: %s", str(e))
            return

        silence_id = alertmanager.downtime(
            reason=self.admin_reason,
            matchers=[
                {"name": "project", "value": self.common_opts.project, "isRegex": False},
                {"name": "instance", "value": self.name_to_remove, "isRegex": False},
            ],
            duration=timedelta(minutes=10),
        )

        LOGGER.info("Set Alertmanager silence %s", silence_id)

    def run(self) -> int:
        """Main entry point"""
        if not self.openstack_api.server_exists(self.name_to_remove, cumin_params=CuminParams(print_output=False)):
            LOGGER.warning(
                "Unable to find server %s in project %s. Please review the project and server name.",
                self.name_to_remove,
                self.common_opts.project,
            )
            return 1

        node_fqdn = f"{self.name_to_remove}.{self.common_opts.project}.{self.cluster_name.value}.wikimedia.cloud"
        if self.openstack_api.server_show(self.name_to_remove).get("status") == "SHUTOFF":
            LOGGER.info("Server is shutoff")
            remote = None
            puppet_hosts = None
        else:
            LOGGER.info("Server is running, disabling Puppet agent")
            remote = self.spicerack.remote().query(f"D{{{node_fqdn}}}", use_sudo=True)

            puppet_hosts = self.spicerack.puppet(remote)
            puppet_hosts.disable(self.admin_reason)

        puppet_server_hostname = self._find_puppetserver(node_fqdn=node_fqdn, puppet_hosts=puppet_hosts)
        LOGGER.info("Found Puppet server %s", puppet_server_hostname)

        # if it's the central puppetmaster, this will be handled by wmf_sink
        if puppet_server_hostname not in ("puppet", "puppetmaster.cloudinfra.wmflabs.org"):
            puppet_server = PuppetServer(self.spicerack.remote().query(f"D{{{puppet_server_hostname}}}", use_sudo=True))
            puppet_cert_hostname = self._guess_puppet_cert_hostname(remote, node_fqdn)
            try:
                puppet_server.delete(puppet_cert_hostname)
            except RemoteExecutionError:
                # workaround T360293
                LOGGER.warning("Ignoring certificate destruction failure", exc_info=True)

        self._downtime()

        self.openstack_api.server_delete(name_to_remove=self.name_to_remove)
        return 0
