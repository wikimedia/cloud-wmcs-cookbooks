r"""WMCS openstack - Unset a cloudvirt node maintenance

Usage example: wmcs.openstack.cloudvirt.unset_maintenance \
    --fqdn cloudvirt1013.eqiad.wmnet

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.alerts import uptime_host
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.openstack.common import AGGREGATES_FILE_PATH, OpenstackAPI, OpenstackNotFound, get_node_cluster_name

LOGGER = logging.getLogger(__name__)


class UnsetMaintenance(CookbookBase):
    """WMCS Openstack cookbook to unset a cloudvirt node maintenance."""

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
            help="FQDN of the cloudvirt to unset maintenance of.",
        )
        parser.add_argument(
            "--aggregates",
            required=False,
            default=None,
            help=(
                "Comma separated list of aggregate names to put the host in (by default will try to "
                f"use {AGGREGATES_FILE_PATH} if it exists, and fail otherwise). A safe choice would be just `ceph`"
            ),
        )
        parser.add_argument(
            "--downtime-id",
            required=False,
            default=None,
            help="Downtime id that you got when downtiming before, otherwise will remove all downtimes.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, UnsetMaintenanceRunner,)(
            fqdn=args.fqdn,
            aggregates=args.aggregates,
            downtime_id=args.downtime_id,
            spicerack=self.spicerack,
        )


class UnsetMaintenanceRunner(WMCSCookbookRunnerBase):
    """Runner for UnsetMaintenance."""

    def __init__(
        self,
        common_opts: CommonOpts,
        fqdn: str,
        spicerack: Spicerack,
        downtime_id: str | None = None,
        aggregates: str | None = None,
    ):
        """Init."""
        self.fqdn = fqdn
        self.openstack_api = OpenstackAPI(remote=spicerack.remote(), cluster_name=get_node_cluster_name(node=self.fqdn))
        self.aggregates = aggregates
        super().__init__(spicerack=spicerack)
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )
        self.downtime_id = downtime_id

    def run_with_proxy(self) -> None:
        """Main entry point."""
        hostname = self.fqdn.split(".", 1)[0]
        try:
            self.openstack_api.aggregate_remove_host(aggregate_name="maintenance", host_name=hostname)
        except OpenstackNotFound as error:
            logging.info("%s", error)

        if self.aggregates:
            aggregates_to_add = [aggregate.strip() for aggregate in self.aggregates.split(",")]
        else:
            aggregates_to_add = [
                aggregate["name"]
                for aggregate in self.openstack_api.aggregate_load_from_host(
                    host=self.spicerack.remote().query(self.fqdn)
                )
            ]

        for aggregate_name in aggregates_to_add:
            try:
                self.openstack_api.aggregate_add_host(aggregate_name=aggregate_name, host_name=hostname)
            except OpenstackNotFound as error:
                logging.info("%s", error)

        uptime_host(spicerack=self.spicerack, host_name=hostname, silence_id=self.downtime_id)
        aggregates_str = ",".join(aggregates_to_add)
        self.sallogger.log(message=f"unset {self.fqdn} maintenance (aggregates: {aggregates_str})")
        LOGGER.info(
            "Host %s now in out of maintenance mode. New VMs will be scheduled in it (aggregates: %s).",
            self.fqdn,
            aggregates_str,
        )
