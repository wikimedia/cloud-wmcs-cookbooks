"""WMCS openstack - set cloudweb nodes in maintenance mode

Usage example: wmcs.openstack.cloudweb.set_maintenance \
    --deployment eqiad1

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.alerts import downtime_host
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName

LOGGER = logging.getLogger(__name__)


class SetMaintenance(CookbookBase):
    """WMCS Openstack cookbook to set a cloudweb node in maintenance."""

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
            "--deployment",
            required=False,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            default=OpenstackClusterName.EQIAD1,
            help="Deployment name to operate on",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            SetMaintenanceRunner,
        )(
            deployment=args.deployment,
            spicerack=self.spicerack,
        )


class SetMaintenanceRunner(WMCSCookbookRunnerBase):
    """Runner for SetMaintenance."""

    def __init__(
        self,
        common_opts: CommonOpts,
        deployment: OpenstackClusterName,
        spicerack: Spicerack,
    ):
        """Init."""
        self.deployment = deployment
        self.spicerack = spicerack
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)

    def run_with_proxy(self) -> None:
        """Main entry point."""
        query = "P{R:Class = role::wmcs::openstack::%s::cloudweb}" % self.deployment
        remote_hosts = self.spicerack.remote().query(query, use_sudo=True)

        downtime_ids = []
        for host in remote_hosts.hosts:
            print("host: %s" % host)
            hostname = host.split(".", 1)[0]
            downtime_ids.append(
                downtime_host(spicerack=self.spicerack, host_name=hostname, comment="Setting maintenance mode.")
            )

        # Also downtime the lvs alert if we're in eqiad1
        if self.deployment == OpenstackClusterName.EQIAD1:
            downtime_host(spicerack=self.spicerack, host_name="labweb-ssl", comment="Setting maintenance mode.")

        remote_hosts.run_sync("touch /etc/openstack-dashboard/maintenance.mode")
        remote_hosts.run_sync("systemctl reload apache2")

        self.sallogger.log(
            message=f"Put cloudweb hosts ({remote_hosts.hosts}) into maintenance mode "
            f"(downtime id: {downtime_ids}, use this to unset)"
        )
        LOGGER.info("Hosts %s now in maintenance mode.", remote_hosts.hosts)
