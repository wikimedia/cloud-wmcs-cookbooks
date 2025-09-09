"""WMCS openstack - remove cloudweb nodes from maintenance mode

Usage example: wmcs.openstack.cloudweb.unset_maintenance \
    --deployment eqiad1

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.alerts import remove_silence
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName

LOGGER = logging.getLogger(__name__)


class SetMaintenance(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
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

        return with_common_opts(
            self.spicerack,
            args,
            SetMaintenanceRunner,
        )(
            deployment=args.deployment,
            spicerack=self.spicerack,
        )


class SetMaintenanceRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        deployment: OpenstackClusterName,
        spicerack: Spicerack,
    ):

        self.deployment = deployment
        self.spicerack = spicerack
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)

    def run_with_proxy(self) -> None:

        query = "P{R:Class = role::wmcs::openstack::%s::cloudweb}" % self.deployment
        remote_hosts = self.spicerack.remote().query(query, use_sudo=True)

        remote_hosts.run_sync("rm -f /etc/openstack-dashboard/maintenance.mode")
        remote_hosts.run_sync("systemctl reload apache2")

        for host in remote_hosts.hosts:
            print("host: %s" % host)
            hostname = host.split(".", 1)[0]
            remove_silence(spicerack=self.spicerack, host_name=hostname)

        if self.deployment == OpenstackClusterName.EQIAD1:
            remove_silence(spicerack=self.spicerack, host_name="labweb-ssl")

        self.sallogger.log(message=f"Removed cloudweb hosts ({remote_hosts.hosts}) from maintenance mode.")
        LOGGER.info("Hosts %s now out of maintenance mode.", remote_hosts.hosts)
