r"""WMCS Ceph - Generic cookbook to upgrade a ceph node.

Usage example:
    cookbook wmcs.ceph.upgrade_ceph_node \
        --to-upgrade-fqdn cloudcephosd2001-dev.codfw.wmnet

"""
from __future__ import annotations

import argparse
import datetime
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.alerts import downtime_host, uptime_host
from wmcs_libs.ceph import CephClusterController, CephOSDFlag, get_node_cluster_name
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, run_one_raw, with_common_opts

LOGGER = logging.getLogger(__name__)


class UpgradeCephNode(CookbookBase):
    """WMCS Ceph cookbook to upgrade a node."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--to-upgrade-fqdn",
            required=True,
            help="FQDN of the node to upgrade",
        )
        parser.add_argument(
            "--skip-maintenance",
            required=False,
            action="store_true",
            help="If set, will not put the cluster into maintenance nor take it out of it.",
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
        return with_common_opts(self.spicerack, args, UpgradeCephNodeRunner)(
            to_upgrade_fqdn=args.to_upgrade_fqdn,
            skip_maintenance=args.skip_maintenance,
            force=args.force,
            spicerack=self.spicerack,
        )


class UpgradeCephNodeRunner(WMCSCookbookRunnerBase):
    """Runner for UpgradeCephNode"""

    def __init__(
        self,
        common_opts: CommonOpts,
        to_upgrade_fqdn: str,
        skip_maintenance: bool,
        force: bool,
        spicerack: Spicerack,
    ):
        """Init"""
        self.to_upgrade_fqdn = to_upgrade_fqdn
        self.force = force
        self.skip_maintenance = skip_maintenance
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.controller = CephClusterController(
            remote=self.spicerack.remote(),
            cluster_name=get_node_cluster_name(to_upgrade_fqdn),
            spicerack=self.spicerack,
        )

    def run_with_proxy(self) -> None:
        """Main entry point"""
        LOGGER.info("Upgrading ceph node %s", self.to_upgrade_fqdn)
        # make sure we make cluster info commands on another node
        self.controller.change_controlling_node()

        if not self.skip_maintenance:
            silences = self.controller.set_maintenance(
                force=self.force, reason=f"Upgrading the ceph node {self.to_upgrade_fqdn}."
            )

        # Can't use sre upgrade-and-reboot due to it using alertmanager's api to downtime hosts
        remote_host = self.spicerack.remote().query(self.to_upgrade_fqdn, use_sudo=True)
        host_name = self.to_upgrade_fqdn.split(".", 1)[0]
        puppet = self.spicerack.puppet(remote_host)
        downtime_id = downtime_host(
            spicerack=self.spicerack,
            host_name=host_name,
            comment="Ceph node software upgrade and reboot",
            duration="20m",
        )
        puppet_reason = self.spicerack.admin_reason("Software upgrade and reboot")

        with puppet.disabled(puppet_reason):
            # Upgrade all packages, leave config files untouched, do not prompt
            upgrade_cmd = [
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "-y",
                "-o",
                "Dpkg::Options::='--force-confdef'",
                "-o",
                "Dpkg::Options::='--force-confold'",
                "dist-upgrade",
            ]
            run_one_raw(command=upgrade_cmd, node=remote_host)

            reboot_time = datetime.datetime.utcnow()
            remote_host.reboot()
            remote_host.wait_reboot_since(reboot_time)

        puppet.run()

        uptime_host(spicerack=self.spicerack, host_name=host_name, silence_id=downtime_id)

        # Once the node is up, let it rebalance
        self.controller.unset_osdmap_flag(CephOSDFlag("norebalance"))
        self.controller.wait_for_cluster_healthy(consider_maintenance_healthy=True, timeout_seconds=300)
        self.controller.set_osdmap_flag(CephOSDFlag("norebalance"))

        if not self.skip_maintenance:
            self.controller.unset_maintenance(force=self.force, silences=silences)
