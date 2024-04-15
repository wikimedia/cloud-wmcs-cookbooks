r"""WMCS Ceph - Destroy the OSD daemons on the given OSD Host

Usage example:
    cookbook wmcs.ceph.osd.depool_and_destroy \
        --osd-hostname cloudcephosd1001 \
        --osd-id 22 \
        --osd-id 23 \
        --task-id T12345

    cookbook wmcs.ceph.osd.depool_and_destroy \
        --osd-hostname cloudcephosd1001 \
        --all-osds \
        --task-id T12345

"""

# pylint: disable=too-many-arguments
from __future__ import annotations

import argparse
import logging
import time
from datetime import timedelta
from typing import cast

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation

from wmcs_libs.ceph import (
    CephClusterController,
    CephException,
    CephOSDFlag,
    CephOSDNodeController,
    OSDTreeOSDNode,
    get_node_cluster_name,
)
from wmcs_libs.common import (
    CommonOpts,
    SALLogger,
    WMCSCookbookRunnerBase,
    add_common_opts,
    parser_type_str_hostname,
    with_common_opts,
)

LOGGER = logging.getLogger(__name__)


class DepoolAndDestroy(CookbookBase):
    """WMCS Ceph cookbook to destroy an OSD daemon with a new one."""

    title = __doc__  # type: ignore

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--osd-hostname",
            required=True,
            action="store",
            type=parser_type_str_hostname,
            help="Hostname of the host running the OSDs to destroy.",
        )
        parser.add_argument(
            "--yes-i-know-what-im-doing",
            required=False,
            action="store_true",
            help=(
                "If passed, will not ask for confirmation. WARNING: this might cause data loss, use only when you are "
                "sure what you are doing."
            ),
        )
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="If passed, will continue even if the cluster is not in a healthy state.",
        )
        parser.add_argument(
            "--osd-id",
            required=False,
            action="append",
            type=int,
            help=(
                "If passed, will only destroy the given OSD daemon ids. Use multiple times to destroy more than one "
                "osd."
            ),
        )
        parser.add_argument(
            "--all-osds",
            required=False,
            action="store_true",
            help="If passed, will destroy all osds registered on the host.",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        if not args.all_osds and not args.osd_id:
            raise Exception("No --osd-id passed, and no --all-osds passed, please pass one of the two.")

        return with_common_opts(self.spicerack, args, DestroyRunner)(
            osd_hostname=args.osd_hostname,
            yes_i_know=args.yes_i_know_what_im_doing,
            only_ids=args.osd_id,
            all_osds=args.all_osds,
            force=args.force,
            only_check=self.spicerack.dry_run,
            spicerack=self.spicerack,
        )


def check_that_osds_belong_to_host(osd_ids: list[int], hostname: str, ceph_controller: CephClusterController) -> None:
    """Check if all the given osds belong to the given host in the cluster managed by the controller.

    Will raise an exception if they are not.
    """
    host_tree_nodes = ceph_controller.get_osd_tree().get_nodes_by_type(wanted_type="host")
    for host_entry in host_tree_nodes:
        if host_entry.name != hostname:
            continue

        gotten_osds_ids = set(cast(OSDTreeOSDNode, osd_data).osd_id for osd_data in host_entry.children)
        if set(osd_ids).issubset(gotten_osds_ids):
            return

        raise Exception(
            f"Not all the osds {osd_ids} are assigned to the host {hostname} (assigned osds are {gotten_osds_ids})"
        )

    raise Exception(f"Unable to find host {hostname} on the cluster {ceph_controller.cluster_name}.")


class DestroyRunner(WMCSCookbookRunnerBase):
    """Runner for Destroy"""

    def __init__(
        self,
        common_opts: CommonOpts,
        osd_hostname: str,
        force: bool,
        yes_i_know: bool,
        only_check: bool,
        only_ids: list[int],
        all_osds: bool,
        spicerack: Spicerack,
    ):
        """Init"""
        self.yes_i_know = yes_i_know
        self.common_opts = common_opts
        self.osd_hostname = osd_hostname
        self.force = force
        self.only_check = only_check
        self.ids = only_ids
        self.all_osds = all_osds

        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)
        cluster_name = get_node_cluster_name(self.osd_hostname)
        self.cluster_controller = CephClusterController(
            remote=self.spicerack.remote(), cluster_name=cluster_name, spicerack=self.spicerack
        )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        if self.all_osds:
            self.ids = self.cluster_controller.get_host_osds(osd_host=self.osd_hostname)

        if not self.yes_i_know:
            ask_confirmation(
                f"This will depool and delete the osds {self.ids} from host {self.osd_hostname}, no data or service "
                "loss are expected, but HA might be degraded until full rebalance, are you sure? (use --yes-i-know to "
                "avoid this question)"
            )

        if not self.force:
            try:
                self.cluster_controller.get_cluster_status().check_healthy()
            except CephException as error:
                LOGGER.exception("Cluster is not in a healthy status: %s", str(error))
                raise

        check_that_osds_belong_to_host(
            osd_ids=self.ids, hostname=self.osd_hostname, ceph_controller=self.cluster_controller
        )

        if self.cluster_controller.is_osdmap_flag_set(
            CephOSDFlag.NOREBALANCE
        ) or self.cluster_controller.is_osdmap_flag_set(CephOSDFlag.NOOUT):
            raise Exception(
                "Can't depool or destroy osds while the cluster has 'noout' or 'norebalance' set, that "
                "might cause an outage, please unset those flags and retry."
            )

        self.sallogger.log(
            message=(
                f"Depooling OSDs with ids in {self.ids} on {self.osd_hostname} from "
                f"{self.cluster_controller.cluster_name}"
            ),
        )
        failures = self.cluster_controller.check_osds_ok_to_stop(osd_ids=self.ids)
        if failures:
            raise Exception("\n".join(failures))

        if self.only_check:
            LOGGER.info("Skipping depooling the OSD daemons, note that it might fail the next check before destroying.")
        else:
            # we already checked that it was safe
            self.cluster_controller.drain_osds(osd_ids=self.ids, be_unsafe=True)

            # the rebalance might take a very very long time, setting timeout to 12h
            timeout = timedelta(hours=12)
            LOGGER.info("Waiting for the cluster to rebalance all the data (timeout of %s)...", timeout)
            # first sleep to allow the cluster to start rebalancing
            time.sleep(60)
            self.cluster_controller.wait_for_in_progress_events(timeout=timeout)
            self.cluster_controller.wait_for_rebalance(timeout=timeout)
            LOGGER.info("Rebalancing done, will stop the OSD daemons service.")

            osd_fqdn = f"{self.osd_hostname}.{self.cluster_controller.cluster_name.get_site().value}.wmnet"
            osd_controller = CephOSDNodeController(remote=self.spicerack.remote(), node_fqdn=osd_fqdn)
            osd_controller.stop_osds(osd_ids=self.ids)

        self.sallogger.log(
            message=(
                f"Destroying OSDs with ids in {self.ids} on {self.osd_hostname} from "
                f"{self.cluster_controller.cluster_name}"
            ),
        )
        failures = self.cluster_controller.check_osds_safe_to_destroy(osd_ids=self.ids)
        if failures:
            raise Exception("\n".join(failures))

        removed_host_msg = ""
        if self.only_check:
            LOGGER.info("Skipping destroying the OSD daemons")
        else:
            for osd_id in self.ids:
                # we already checked that it was safe
                self.cluster_controller.destroy_osd(osd_id=osd_id, be_unsafe=True)

            if not self.cluster_controller.get_host_osds(osd_host=self.osd_hostname):
                LOGGER.info("Cleaning up empty host bucket in the CRUSH map.")
                self.cluster_controller.remove_crush_bucket(bucket_name=self.osd_hostname)
                removed_host_msg = f" and removed the OSD host {self.osd_hostname} from the CRUSH map"
            else:
                LOGGER.info("Not cleaning up host bucket, as it still has some OSDs in it")

        self.sallogger.log(message=f"Depooled and destroyed OSD daemons {self.ids}{removed_host_msg}.")
