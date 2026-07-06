r"""WMCS openstack - Unset a cloudvirt node maintenance

Usage example: wmcs.openstack.cloudvirt.unset_maintenance \
    --fqdn cloudvirt1013.eqiad.wmnet

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.openstack.common import OpenstackAPI, get_node_cluster_name

LOGGER = logging.getLogger(__name__)


class UnsetMaintenance(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--fqdn",
            required=True,
            help="FQDN of the cloudvirt to unset maintenance of.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(
            self.spicerack,
            args,
            UnsetMaintenanceRunner,
        )(
            fqdn=args.fqdn,
            spicerack=self.spicerack,
        )


class UnsetMaintenanceRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        fqdn: str,
        spicerack: Spicerack,
    ):

        self.fqdn = fqdn
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=get_node_cluster_name(node=self.fqdn),
        )
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)

    def run_with_proxy(self) -> None:

        hostname = self.fqdn.split(".", 1)[0]
        self.openstack_api.compute_service_enable(host=hostname, service="nova-compute")

        self.sallogger.log(message=f"unset {self.fqdn} maintenance")
        LOGGER.info(
            "Host %s now out of maintenance mode. New VMs will be scheduled in it.",
            self.fqdn,
        )
