r"""WMCS Ceph - Drain all the osd daemons from a host or set of hosts

Usage example:
    cookbook wmcs.ceph.drain_node \
        --osd-hostname cloudcephosd2001-dev \
        --osd-hostname cloudcephosd2002-dev \
        --cluster-name codfw1

"""

from __future__ import annotations

import argparse
import datetime
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.alerts import remove_silence, silence_host
from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

LOGGER = logging.getLogger(__name__)


class DrainNode(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to roll restart.",
        )
        parser.add_argument(
            "--osd-hostname",
            required=True,
            action="append",
            help=(
                "Hostname of the new OSDs to add. Repeat for each new OSD. If specifying more "
                "than one, consider passing --yes-i-know-what-im-doing"
            ),
        )
        parser.add_argument(
            "--set-maintenance",
            required=False,
            default=False,
            action="store_true",
            help=(
                "If passed, it will set the cluster in maintenance mode (careful! It will not rebalance data so you "
                "might render the cluster unavailable)."
            ),
        )
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="If passed, will continue even if the cluster is not in a healthy state.",
        )
        parser.add_argument(
            "--no-wait",
            required=False,
            action="store_true",
            help=(
                "If passed, it will not wait for the cluster to finish rebalancing (note that if it does "
                "not have to rebalance, might wait forever for the rebalancing to start)."
            ),
        )
        parser.add_argument(
            "--osd-id",
            required=False,
            action="append",
            type=int,
            help=(
                "If passed, will only undrain the given OSD daemon ids. Use multiple times to destroy more than one "
                "osd."
            ),
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(
            self.spicerack,
            args,
            DrainNodeRunner,
        )(
            osd_hostnames=args.osd_hostname,
            osd_ids=args.osd_id,
            set_maintenance=args.set_maintenance,
            cluster_name=args.cluster_name,
            force=args.force,
            wait=not args.no_wait,
            spicerack=self.spicerack,
        )


class DrainNodeRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        osd_hostnames: list[str],
        osd_ids: list[int],
        cluster_name: CephClusterName,
        force: bool,
        wait: bool,
        set_maintenance: bool,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments

        self.common_opts = common_opts
        self.osd_hostnames = osd_hostnames
        self.osd_ids = osd_ids
        self.set_maintenance = set_maintenance
        self.force = force
        self.wait = wait
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(),
            cluster_name=cluster_name,
            spicerack=self.spicerack,
        )
        cluster_nodes = self.controller.get_nodes()["osd"]
        for host in self.osd_hostnames:
            if host not in cluster_nodes:
                raise Exception(f"Host {host} is not in the cluster {', '.join(cluster_nodes.keys())}")

    def run_with_proxy(self) -> None:

        LOGGER.info("Draining nodes %s, osds %s", self.osd_hostnames, self.osd_ids if self.osd_ids else "all")

        if not self.force:
            self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True)

        if self.set_maintenance:
            cluster_silences = self.controller.set_maintenance(
                task_id=self.common_opts.task_id,
                reason=f"Draining node {self.osd_hostnames}",
            )
        else:
            cluster_silences = []

        for idx, maybe_host_name in enumerate(self.osd_hostnames):
            host_name = maybe_host_name.split(".", 1)[0]
            LOGGER.info(
                "[%s] Draining node %s, osds %s (%d/%d)",
                datetime.datetime.now(),
                maybe_host_name,
                self.osd_ids if self.osd_ids else "all",
                idx,
                len(self.osd_hostnames),
            )
            silence_id = silence_host(
                spicerack=self.spicerack,
                host_name=host_name,
                comment="Draining with wmcs.ceph.drain_node",
                task_id=self.common_opts.task_id,
                # A bit longer than the timeout for the operation
                duration=datetime.timedelta(hours=6),
            )

            self.controller.drain_osd_node(
                osd_host=host_name,
                be_unsafe=self.force,
                wait=self.wait,
                batch_size=2,
                osd_ids=self.osd_ids,
            )

            if self.force:
                LOGGER.info("Force passed, ignoring cluster health and continuing")
            else:
                LOGGER.info(
                    "[%s] Drained node %s (%d/%d)",
                    datetime.datetime.now(),
                    maybe_host_name,
                    idx,
                    len(self.osd_hostnames),
                )
                self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True)
                remove_silence(spicerack=self.spicerack, silence_id=silence_id)
                LOGGER.info("[%s] Cluster healthy, continuing", datetime.datetime.now())

        if self.set_maintenance:
            self.controller.unset_maintenance(silences=cluster_silences)

        LOGGER.info("Finished draining nodes %s", self.osd_hostnames)
