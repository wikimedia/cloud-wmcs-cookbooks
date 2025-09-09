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
from wmcs_libs.openstack.common import AGGREGATES_FILE_PATH, OpenstackAPI, OpenstackNotFound, get_node_cluster_name

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
        parser.add_argument(
            "--aggregates",
            required=False,
            default=None,
            help=(
                "Comma separated list of aggregate names to put the host in (by default will try to "
                f"use {AGGREGATES_FILE_PATH} if it exists, and fail otherwise). A safe choice would be just `ceph`"
            ),
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(
            self.spicerack,
            args,
            UnsetMaintenanceRunner,
        )(
            fqdn=args.fqdn,
            aggregates=args.aggregates,
            spicerack=self.spicerack,
        )


class UnsetMaintenanceRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        fqdn: str,
        spicerack: Spicerack,
        aggregates: str | None = None,
    ):

        self.fqdn = fqdn
        self.openstack_api = OpenstackAPI(remote=spicerack.remote(), cluster_name=get_node_cluster_name(node=self.fqdn))
        self.aggregates = aggregates
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)

    def run_with_proxy(self) -> None:

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

            if aggregates_to_add == ["maintenance"]:
                raise RuntimeError(
                    f"Host {self.fqdn} thinks it should be in 'maintenance' aggregate, "
                    "specify real aggregate with --aggregate"
                )

        for aggregate_name in aggregates_to_add:
            try:
                self.openstack_api.aggregate_add_host(aggregate_name=aggregate_name, host_name=hostname)
            except OpenstackNotFound as error:
                logging.info("%s", error)

        aggregates_str = ",".join(aggregates_to_add)
        self.sallogger.log(message=f"unset {self.fqdn} maintenance (aggregates: {aggregates_str})")
        LOGGER.info(
            "Host %s now in out of maintenance mode. New VMs will be scheduled in it (aggregates: %s).",
            self.fqdn,
            aggregates_str,
        )
