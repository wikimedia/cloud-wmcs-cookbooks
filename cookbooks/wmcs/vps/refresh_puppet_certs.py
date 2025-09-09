r"""WMCS VPS - Remove and regenerate the puppet certificates of the host.

Usage example: wmcs.vps.refresh_puppet_certs \
    --fqdn tools-host.tools.eqiad1.wikimedia.cloud

"""

from __future__ import annotations

import argparse
import logging

from cumin.transports import Command
from spicerack import RemoteHosts, Spicerack
from spicerack.cookbook import CookbookBase
from spicerack.puppet import PuppetHosts, PuppetServer
from spicerack.remote import RemoteExecutionError

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, run_one_raw, with_common_opts
from wmcs_libs.inventory.libs import get_openstack_project_deployment

LOGGER = logging.getLogger(__name__)


def _get_puppetserver(spicerack: Spicerack, remote_host: RemoteHosts, puppetmaster: str) -> PuppetServer:
    puppetserver_fqdn = puppetmaster
    if puppetserver_fqdn == "puppet":
        puppetserver_fqdn = run_one_raw(
            node=remote_host, command=["dig", "+short", "-x", "$(dig +short puppet)"]
        ).strip()
        # remove the extra dot that dig appends
        puppetserver_fqdn = puppetserver_fqdn[:-1]

    return PuppetServer(
        server_host=spicerack.remote().query(
            f"D{{{puppetserver_fqdn}}}",
            use_sudo=True,
        )
    )


def _refresh_cert(
    spicerack: Spicerack,
    remote_host: RemoteHosts,
) -> None:
    """Takes care of the dance to remove and regenerate a cert on the host and it's puppetserver."""
    node_to_bootstrap = PuppetHosts(remote_hosts=remote_host)
    fqdn = str(remote_host)
    puppetservers = node_to_bootstrap.get_ca_servers()
    puppetserver = _get_puppetserver(
        spicerack=spicerack,
        remote_host=remote_host,
        puppetmaster=puppetservers[fqdn],
    )
    try:
        puppetserver.destroy(hostname=fqdn)
    except RemoteExecutionError:
        LOGGER.warning(
            "Ignoring certificate destruction failure, probably first run moving to new server", exc_info=True
        )
        # workaround T360293

    cert_fingerprint = node_to_bootstrap.regenerate_certificate()[fqdn]
    cert = puppetserver.get_certificate_metadata(hostname=fqdn)
    if cert["state"] == PuppetServer.PUPPET_CERT_STATE_SIGNED:
        # the cert exists and is already signed
        return

    puppetserver.sign(
        hostname=fqdn,
        fingerprint=cert_fingerprint,
    )


class RefreshPuppetCerts(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser, project_default=None)
        parser.add_argument(
            "--fqdn",
            required=True,
            help="FQDN of the to bootstrap (ex. toolsbeta-test-k8s-etcd-9.toolsbeta.eqiad1.wikimedia.cloud)",
        )
        parser.add_argument(
            "--pre-run-puppet",
            action="store_true",
            help="If passed, will force a puppet run (ignoring the results) before refreshing the certs.",
        )
        parser.add_argument(
            "--ignore-failures",
            action="store_true",
            help="If passed, will ignore any failures when running puppet.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        args.project, _ = get_openstack_project_deployment(args.fqdn)
        return with_common_opts(self.spicerack, args, RefreshPuppetCertsRunner)(
            fqdn=args.fqdn,
            pre_run_puppet=args.pre_run_puppet,
            ignore_failures=args.ignore_failures,
            spicerack=self.spicerack,
        )


class RefreshPuppetCertsRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        fqdn: str,
        pre_run_puppet: bool,
        ignore_failures: bool,
        spicerack: Spicerack,
    ):

        self.fqdn = fqdn
        self.pre_run_puppet = pre_run_puppet
        self.ignore_failures = ignore_failures
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"on {self.fqdn}"

    def run(self) -> None:
        """
        Basic process:
            Refresh certs on current puppetserver (in case the fqdn already existed)
            Try to run puppet (pulls new puppetserver if needed, might fail)
            If there is a new puppetserver, refresh certs on those
            If there was a new puppetserver or the first puppet run failed, run puppet again
        """
        remote_host = self.spicerack.remote().query(f"D{{{self.fqdn}}}", use_sudo=True)
        node_to_bootstrap = PuppetHosts(remote_hosts=remote_host)
        pre_run_passed = False

        # For the first run, make sure that the current master has no cert with this fqdn
        pre_puppetservers = node_to_bootstrap.get_ca_servers()
        _refresh_cert(spicerack=self.spicerack, remote_host=remote_host)

        if self.pre_run_puppet:
            try:
                node_to_bootstrap.run()
                pre_run_passed = True
            except RemoteExecutionError:
                if self.ignore_failures:
                    pass
                else:
                    raise

        else:
            # We have to make sure in any case that the puppet config is refreshed to do the puppetservers switch.
            # The tag makes only run the puppet config related manifests.
            run_one_raw(
                node=remote_host, command=Command("puppet agent --test --tags profile::puppet::agent", ok_codes=[])
            )

        post_puppetservers = node_to_bootstrap.get_ca_servers()
        if post_puppetservers != pre_puppetservers:
            _refresh_cert(spicerack=self.spicerack, remote_host=remote_host)

        if post_puppetservers == pre_puppetservers or not pre_run_passed:
            try:
                node_to_bootstrap.run()
            except RemoteExecutionError:
                if self.ignore_failures:
                    pass
                else:
                    raise
