r"""WMCS Ceph - Bootstrap a new osd

Usage example:
    cookbook wmcs.ceph.osd.bootstrap_and_add \
        --new-osd-fqdn cloudcephosd1016.eqiad.wmnet \
        --task-id T12345

"""

# pylint: disable=too-many-arguments
from __future__ import annotations

import argparse
import logging
import time
from typing import Callable, cast

from spicerack import RemoteHosts, Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from spicerack.puppet import PuppetHosts

from cookbooks.wmcs.ceph.reboot_node import RebootNode
from wmcs_libs.ceph import CephClusterController, CephOSDFlag, CephOSDNodeController, OSDClass, OSDTreeOSDNode
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

LOGGER = logging.getLogger(__name__)


class BootstrapAndAdd(CookbookBase):
    """WMCS Ceph cookbook to bootstrap and add a new OSD."""

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
            "--skip-reboot",
            required=False,
            action="store_true",
            help=(
                "If passed, will not do the first reboot before adding the new osds. Useful when the machine has "
                "already some running OSDs and you are sure the reboot is not needed."
            ),
        )
        parser.add_argument(
            "--only-check",
            required=False,
            action="store_true",
            help="If passed, will only run the pre-setup checks on the host and report back, nothing more.",
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
            "--batch-size",
            required=False,
            default=4,
            help="Number of osds to bring up at a time to avoid congesting the network, use 0 for all at once.",
        )
        parser.add_argument(
            "--wait-for-rebalance",
            required=False,
            action="store_true",
            help=(
                "If passed, will wait for the cluster to do the rebalancing after adding the new OSDs. Note that this "
                "might take several hours."
            ),
        )
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="If passed, will continue even if the cluster is not in a healthy state.",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, BootstrapAndAddRunner)(
            cluster_name=args.cluster_name,
            osd_hostnames=args.osd_hostname,
            yes_i_know=args.yes_i_know_what_im_doing,
            skip_reboot=args.skip_reboot,
            wait_for_rebalance=args.wait_for_rebalance,
            force=args.force,
            batch_size=args.batch_size,
            only_check=args.only_check,
            spicerack=self.spicerack,
        )


def _wait_for_osds_to_show_up(cluster_controller: CephClusterController, ceph_hostname: str) -> list[OSDTreeOSDNode]:
    osd_tree = cluster_controller.get_osd_tree()
    retries: int = 0
    while not cluster_controller.is_osd_host_valid(osd_tree=osd_tree, hostname=ceph_hostname):
        time.sleep(5)
        retries += 1
        if retries > 10:
            raise Exception(f"The new OSD node ({ceph_hostname}) is not in the OSD tree, or is not as expected")
        osd_tree = cluster_controller.get_osd_tree()

    LOGGER.info("All OSDs are showing up in the cluster, continuing.")
    for host in osd_tree.get_nodes_by_type(wanted_type="host"):
        if host.name == ceph_hostname:
            return cast(list[OSDTreeOSDNode], host.children)

    raise Exception(f"Something went wrong, unable to find host {ceph_hostname} in the osd tree {osd_tree}")


class BootstrapAndAddRunner(WMCSCookbookRunnerBase):
    """Runner for BootstrapAndAdd"""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: CephClusterName,
        osd_hostnames: list[str],
        force: bool,
        yes_i_know: bool,
        skip_reboot: bool,
        wait_for_rebalance: bool,
        only_check: bool,
        batch_size: int,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.osd_fqdns = [
            hostname.split(".", 1)[0] + f".{cluster_name.get_site().get_domain()}" for hostname in osd_hostnames
        ]
        self.force = force
        self.yes_i_know = yes_i_know
        self.skip_reboot = skip_reboot
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.wait_for_rebalance = wait_for_rebalance
        self.only_check = only_check
        self.batch_size = batch_size
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)
        self.cluster_controller = CephClusterController(
            remote=self.spicerack.remote(), cluster_name=cluster_name, spicerack=self.spicerack
        )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        self.sallogger.log(
            message=f"Adding all available disks from nodes {self.osd_fqdns} to the cluster",
        )
        silences: list[str] = []
        if self.only_check:
            LOGGER.info("Skipping setting the cluster as in maintenance, only checking")
        else:
            silences = self.cluster_controller.downtime_cluster_alerts(
                task_id=self.common_opts.task_id, reason=f"Adding hosts {self.osd_fqdns} to the cluster"
            )
            # this avoids rebalancing after each osd is added
            self.cluster_controller.set_osdmap_flag(CephOSDFlag.NOREBALANCE)
            self.cluster_controller.set_osdmap_flag(CephOSDFlag.NOIN)

        only_check_str = ""
        if self.only_check:
            only_check_str = "<only check>"

        for index, new_osd_fqdn in enumerate(self.osd_fqdns):

            def info(msg: str, osd_node: str = new_osd_fqdn) -> None:
                LOGGER.info("[%s] %s %s", osd_node, only_check_str, msg)

            def sal_info(msg: str, osd_node: str = new_osd_fqdn) -> None:
                self.sallogger.log(f"[{osd_node}]{only_check_str} {msg}")

            sal_info(f"Starting... ({index + 1}/{len(self.osd_fqdns)})")
            node: RemoteHosts = self.spicerack.remote().query(f"D{{{new_osd_fqdn}}}", use_sudo=True)
            osd_controller = CephOSDNodeController(remote=self.spicerack.remote(), node_fqdn=new_osd_fqdn)

            new_devices = osd_controller.get_available_devices()
            if not new_devices:
                info(f"No new devices found on host {new_osd_fqdn}, skipping...")
                continue

            info(f"Found available disks {new_devices} on node {new_osd_fqdn}")

            if not self.skip_reboot:
                info("Running puppet and rebooting to make sure we start from fresh boot.")
                self._do_reboot_and_puppet(node=node, host_fqdn=new_osd_fqdn)

            info("Doing some checks...")
            self._do_checks(host_fqdn=new_osd_fqdn, osd_controller=osd_controller)
            info("checks OK")

            if self.only_check:
                info("Skipping adding the new devices, fixing their class and undraining")
                continue

            osd_controller.add_all_available_devices(interactive=(not self.yes_i_know))
            self._fix_osd_classes(host_fqdn=new_osd_fqdn, info=info)
            sal_info(
                f"Added all available disks ({new_devices}) from node {new_osd_fqdn}... "
                f"({index + 1}/{len(self.osd_fqdns)})",
            )

            info(
                "The new OSDs are up and running, the cluster will now start rebalancing the data to them, that might "
                "take quite a long time, you can also follow the progress by running 'ceph status' on a control node."
            )
            info(
                f"We'll add the new osds in batches of {self.batch_size or len(new_devices)}, "
                "to avoid saturating the network"
            )
            self._undrain_in_batches(
                host_fqdn=new_osd_fqdn,
                batch_size=self.batch_size,
                wait_for_rebalance=self.wait_for_rebalance,
                new_devices=new_devices,
            )

        if silences:
            self.cluster_controller.uptime_cluster_alerts(silences=silences)

        self.sallogger.log(
            message=f"Added {len(self.osd_fqdns)} new OSDs {self.osd_fqdns} \\o/",
        )

    def _do_reboot_and_puppet(self, node: RemoteHosts, host_fqdn: str) -> None:
        PuppetHosts(remote_hosts=node).run()
        reboot_node_cookbook = RebootNode(spicerack=self.spicerack)
        reboot_args = [
            "--skip-maintenance",
            "--fqdn-to-reboot",
            host_fqdn,
        ]
        if self.force:
            reboot_args += ["--force"]

        reboot_args += self.common_opts.to_cli_args()

        reboot_node_cookbook.get_runner(args=reboot_node_cookbook.argument_parser().parse_args(reboot_args)).run()
        # Puppet adds the network routes to the cluster network on run
        # so we need to run it once after reboot
        PuppetHosts(remote_hosts=node).run()

    def _do_checks(self, host_fqdn: str, osd_controller: CephOSDNodeController) -> None:
        node_failures = self.cluster_controller.check_if_osd_ready_for_bootstrap(osd_controller=osd_controller)
        if node_failures:
            errors_str = "\n    ".join(node_failures)
            error_msg = f"The node {host_fqdn} is not suitable to be added as an osd:\n    {errors_str}"
            LOGGER.error(error_msg)
            raise Exception(error_msg)

    def _fix_osd_classes(self, host_fqdn: str, info: Callable[[str], None]) -> None:
        new_osds = _wait_for_osds_to_show_up(
            cluster_controller=self.cluster_controller, ceph_hostname=host_fqdn.split(".", 1)[0]
        )
        wrongly_classified_osds = [osd for osd in new_osds if osd.device_class != OSDClass.SSD]
        if wrongly_classified_osds:
            info(f"Got some OSDs with the wrong classes, fixing: {wrongly_classified_osds}")
        for osd in wrongly_classified_osds:
            self.cluster_controller.set_osd_class(osd_id=osd.osd_id, osd_class=OSDClass.SSD)

        new_osds = _wait_for_osds_to_show_up(
            cluster_controller=self.cluster_controller, ceph_hostname=host_fqdn.split(".", 1)[0]
        )
        wrongly_classified_osds = [osd for osd in new_osds if osd.device_class != OSDClass.SSD]
        if wrongly_classified_osds:
            raise Exception(
                f"Something went wrong, I was unable to change the device class for osds {wrongly_classified_osds}"
            )

    def _undrain_in_batches(
        self, host_fqdn: str, batch_size: int, wait_for_rebalance: bool, new_devices: list[str]
    ) -> None:
        ceph_hostname = host_fqdn.split(".", 1)[0]
        _wait_for_osds_to_show_up(cluster_controller=self.cluster_controller, ceph_hostname=ceph_hostname)
        new_osds_ids = self.cluster_controller.get_osd_for_devices(hostname=ceph_hostname, devices=new_devices)
        if not new_osds_ids:
            raise Exception(f"Unable to find new osd for device {new_devices}, something went wrong")

        # marking them all out first as they are in by default
        for osd_id in new_osds_ids:
            self.cluster_controller.crush_reweight_osd(osd_id=osd_id, new_weight=0.0)
            self.cluster_controller.mark_osd_out(osd_id=osd_id)

        # Now we enable rebalancing
        self.cluster_controller.unset_osdmap_flag(CephOSDFlag.NOREBALANCE)
        self.cluster_controller.unset_osdmap_flag(CephOSDFlag.NOIN)

        # And bring them in in batches, we need to give the cluster a few seconds to start rebalancing
        time.sleep(10)
        self.cluster_controller.undrain_osds_in_chunks(
            osd_ids=new_osds_ids, batch_size=batch_size, wait=wait_for_rebalance
        )
