r"""WMCS openstack - Safely reboot a cloudvirt node.

This includes putting in maintenance, draining, and unsetting maintenance.

Usage example: wmcs.openstack.cloudvirt.safe_reboot \
    --fqdn cloudvirt1013.eqiad.wmnet

"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from spicerack import RemoteHosts, Spicerack
from wmflib.interactive import confirm_on_failure

from cookbooks.wmcs.openstack.cloudvirt.drain import Drain
from cookbooks.wmcs.openstack.cloudvirt.unset_maintenance import UnsetMaintenance
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, with_common_opts
from wmcs_libs.openstack.batch import CloudvirtBatchBase, CloudvirtBatchRunnerBase
from wmcs_libs.openstack.common import get_control_nodes

LOGGER = logging.getLogger(__name__)


class SafeReboot(CloudvirtBatchBase):
    """WMCS Openstack cookbook to safe reboot a cloudvirt node."""

    __title__ = __doc__

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            SafeRebootRunner,
        )(
            args=args,
            spicerack=self.spicerack,
        )


class SafeRebootRunner(CloudvirtBatchRunnerBase):
    """Runner for SafeReboot"""

    downtime_reason = "host reboot"

    def __init__(self, common_opts: CommonOpts, args: argparse.Namespace, spicerack: Spicerack):
        super().__init__(common_opts, args, spicerack)
        self.control_node_fqdn = get_control_nodes(cluster_name=self.cluster)[0]

    def _drain(self, fqdn: str) -> None:
        drain_cookbook = Drain(spicerack=self.spicerack)
        runner = drain_cookbook.get_runner(
            args=drain_cookbook.argument_parser().parse_args(
                args=[
                    "--fqdn",
                    fqdn,
                ]
                + self.common_opts.to_cli_args(),
            )
        )

        confirm_on_failure(runner.run)

    def run_on_hosts(self, hosts: RemoteHosts) -> None:
        if len(hosts) != 1:
            raise ValueError("safe_reboot does not support on operating on multiple nodes at once")
        fqdn = str(hosts)

        self._drain(fqdn)

        remote_host = self.spicerack.remote().query(f"D{{{fqdn}}}", use_sudo=True)
        reboot_time = datetime.utcnow()
        LOGGER.info("Rebooting and waiting for %s up", remote_host)
        remote_host.reboot()
        remote_host.wait_reboot_since(reboot_time)

        unset_maintenance_cookbook = UnsetMaintenance(spicerack=self.spicerack)
        unset_maintenance_cookbook.get_runner(
            args=unset_maintenance_cookbook.argument_parser().parse_args(
                args=[
                    "--fqdn",
                    fqdn,
                ]
                + self.common_opts.to_cli_args(),
            )
        ).run()
