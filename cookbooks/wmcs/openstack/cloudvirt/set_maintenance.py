r"""WMCS openstack - set a cloudvirt node in maintenance

Usage example: wmcs.openstack.cloudvirt.set_maintenance \
    --fqdn cloudvirt1013.eqiad.wmnet

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.openstack.common import OpenstackAPI, OpenstackNotFound, get_node_cluster_name

LOGGER = logging.getLogger(__name__)


class SetMaintenance(CookbookBase):
    """WMCS Openstack cookbook to set a cloudvirt node in maintenance."""

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
            "--fqdn",
            required=True,
            help="FQDN of the cloudvirt to set in maintenance.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, SetMaintenanceRunner,)(
            fqdn=args.fqdn,
            spicerack=self.spicerack,
        )


class SetMaintenanceRunner(WMCSCookbookRunnerBase):
    """Runner for SetMaintenance."""

    def __init__(
        self,
        common_opts: CommonOpts,
        fqdn: str,
        spicerack: Spicerack,
    ):
        """Init."""
        self.fqdn = fqdn
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=get_node_cluster_name(node=self.fqdn),
        )
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)

    def run_with_proxy(self) -> None:
        """Main entry point."""
        hostname = self.fqdn.split(".", 1)[0]

        current_aggregates = self.openstack_api.server_get_aggregates(name=hostname)
        aggregate_names = [aggregate["name"] for aggregate in current_aggregates]

        if aggregate_names == ["maintenance"]:
            LOGGER.warning("Host %s is already in maintenance mode", self.fqdn)
            return

        self.openstack_api.aggregate_persist_on_host(
            host=self.spicerack.remote().query(self.fqdn), current_aggregates=current_aggregates
        )

        try:
            for aggregate in aggregate_names:
                self.openstack_api.aggregate_remove_host(aggregate_name=aggregate, host_name=hostname)
        except OpenstackNotFound as error:
            logging.info("%s", error)

        try:
            self.openstack_api.aggregate_add_host(aggregate_name="maintenance", host_name=hostname)
        except OpenstackNotFound as error:
            logging.info("%s", error)

        LOGGER.info("Host %s now in maintenance mode. No new VMs will be scheduled in it.", self.fqdn)
