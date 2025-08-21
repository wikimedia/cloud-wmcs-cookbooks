"""WMCS Ceph - Reactivate an OSD node

After an existing OSD node has been reimaged, the OSD drives
themselves still contain LVM partitions populated with useful
ceph data. Ceph data in the OS is wiped by the reimage, though,
so the reimaged nodes don't immediately join back up with the ceph
cluser.

This script re-activates those nodes (primarily using 'ceph-volume activate')
allowing them to rejoin the cluster with a mimumu of rebalancing.

Ideally an operator has set 'noout' before reimaging nodes; this
cookbook leaves 'noout' set to allow for subsequent manual operations.

Usage example:
    cookbook wmcs.ceph.osd.reactivate \
        --osd-hostname cloudcephosd1016.eqiad.wmnet \
        --task-id T12345

"""

from __future__ import annotations

import argparse
import logging

from spicerack import RemoteHosts

from cookbooks.wmcs.ceph.osd import bootstrap_and_add
from wmcs_libs.ceph import (
    CephOSDFlag,
    CephOSDNodeController,
)
from wmcs_libs.common import WMCSCookbookRunnerBase, with_common_opts

LOGGER = logging.getLogger(__name__)


class Reactivate(bootstrap_and_add.BootstrapAndAdd):
    """WMCS Ceph cookbook to activate an existing OSD after a reimage.

    This assumes that the OSD(s) passed in contain ceph partitions
    which where preserved after a re-image but which need to be activated.
    """

    title = __doc__  # type: ignore

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""

        # Pass in force = True because the cluster is almost
        #  certainly set to noout when this cookbook is run and
        #  we don't want to block on that.
        return with_common_opts(self.spicerack, args, ReactivateRunner)(
            cluster_name=args.cluster_name,
            osd_hostnames=args.osd_hostname,
            yes_i_know=args.yes_i_know_what_im_doing,
            skip_reboot=args.skip_reboot,
            wait_for_rebalance=not args.no_wait,
            force=True,
            batch_size=args.batch_size,
            only_check=args.only_check,
            spicerack=self.spicerack,
            expected_osd_drives=args.expected_osd_drives,
            expected_ceph_version=args.expected_ceph_version,
            os_hw_raid=args.os_hw_raid,
        )


class ReactivateRunner(bootstrap_and_add.BootstrapAndAddRunner):
    """Runner for Reactivate

    a variant of BootstrapAndAdd that expects osd partitions to already
    be formatted and at least partially populated.
    """

    def run_with_proxy(self) -> None:
        """Main entry point"""
        self.sallogger.log(
            message=f"Activating all available osd disks from nodes {self.osd_fqdns} to the cluster",
        )

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

            new_devices = osd_controller.get_inactive_devices()
            if not new_devices:
                info(f"No osd candidate devices found on host {new_osd_fqdn}, skipping...")
                continue

            info(f"Found candidate disks {new_devices} on node {new_osd_fqdn}")

            if self.only_check:
                LOGGER.info("Skipping setting the cluster as in maintenance, only checking")
            else:
                # this avoids rebalancing after each osd is added
                self.cluster_controller.set_osdmap_flag(CephOSDFlag.NOREBALANCE)
                self.cluster_controller.set_osdmap_flag(CephOSDFlag.NOIN)

            if not self.skip_reboot:
                info("Running puppet and rebooting to make sure we start from fresh boot.")
                self._do_reboot_and_puppet(node=node, host_fqdn=new_osd_fqdn)

            info("Doing some checks...")
            self._do_checks(host_fqdn=new_osd_fqdn, osd_controller=osd_controller)
            info("checks OK")

            if self.only_check:
                info("Skipping adding the new devices, fixing their class and undraining")
                continue

            osd_controller.activate_inactive_devices(interactive=(not self.yes_i_know))
            self._fix_osd_classes(host_fqdn=new_osd_fqdn, info=info)
            sal_info(
                f"Added all available disks ({new_devices}) from node {new_osd_fqdn}... "
                f"({index + 1}/{len(self.osd_fqdns)})",
            )

            # Now we enable rebalancing. In most cases very little will
            #  happen since the osds we activated are already populated.
            self.cluster_controller.unset_osdmap_flag(CephOSDFlag.NOREBALANCE)
            self.cluster_controller.unset_osdmap_flag(CephOSDFlag.NOIN)

            info(
                "The new OSDs are up and running, the cluster will now start rebalancing the data to them, that might "
                "take quite a long time, you can also follow the progress by running 'ceph status' on a control node."
            )

        self.sallogger.log(
            message=f"Activated {len(self.osd_fqdns)} OSDs {self.osd_fqdns} \\o/",
        )
