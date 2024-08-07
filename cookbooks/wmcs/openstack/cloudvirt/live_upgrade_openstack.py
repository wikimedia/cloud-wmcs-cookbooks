r"""WMCS openstack - upgrade live (without stopping VMs or rebooting) a cloudvirt node in maintenance

Usage example: wmcs.openstack.cloudvirt.live_upgrade_openstack \
    --fqdn-to-upgrade cloudvirt1013.eqiad.wmnet

"""

from __future__ import annotations

import argparse
import logging

from cumin.transports import Command
from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, run_one_raw, with_common_opts

LOGGER = logging.getLogger(__name__)


class LiveUpgrade(CookbookBase):
    """WMCS Openstack cookbook to upgrade the OpenStack packages on a cloudvirt node."""

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
            "--fqdn-to-upgrade",
            required=True,
            help="FQDN of the cloudvirt to set in maintenance.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, LiveUpgradeRunner)(
            fqdn_to_upgrade=args.fqdn_to_upgrade,
            spicerack=self.spicerack,
        )


class LiveUpgradeRunner(WMCSCookbookRunnerBase):
    """Runner for LiveUpgrade."""

    def __init__(
        self,
        common_opts: CommonOpts,
        fqdn_to_upgrade: str,
        spicerack: Spicerack,
    ):
        """Init."""
        self.fqdn_to_upgrade = fqdn_to_upgrade
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"on host '{self.fqdn_to_upgrade}'"

    def run_with_proxy(self) -> None:
        """Main entry point."""
        node_to_upgrade = self.spicerack.remote().query(f"D{{{self.fqdn_to_upgrade}}}", use_sudo=True)
        run_one_raw(node=node_to_upgrade, command=["puppet", "agent", "--enable"])
        run_one_raw(node=node_to_upgrade, command=Command("run-puppet-agent", ok_codes=[]))
        run_one_raw(node=node_to_upgrade, command=["apt", "update"])
        run_one_raw(
            node=node_to_upgrade,
            command=[
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "install",
                "-y",
                "python3-libvirt",
                "python3-os-vif",
                "nova-compute",
                "neutron-common",
                "nova-compute-kvm",
                "neutron-openvswitch-agent",
                "python3-neutron ",
                "python3-eventlet",
                "python3-oslo.messaging",
                "python3-taskflow",
                "python3-tooz",
                "python3-keystoneauth1",
                "python3-requests",
                "python3-urllib3",
                "-o",
                '"Dpkg::Options::=--force-confdef"',
                "-o",
                '"Dpkg::Options::=--force-confold"',
            ],
        )
        run_one_raw(
            node=node_to_upgrade,
            command=[
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "dist-upgrade",
                "-y",
                "--allow-downgrades",
                "-o",
                '"Dpkg::Options::=--force-confdef"',
                "-o",
                '"Dpkg::Options::=--force-confold"',
            ],
        )
        run_one_raw(node=node_to_upgrade, command=Command("run-puppet-agent", ok_codes=[]))
        run_one_raw(node=node_to_upgrade, command=["systemctl", "restart", "neutron-openvswitch-agent"])
        run_one_raw(node=node_to_upgrade, command=["systemctl", "stop", "libvirtd"])
        run_one_raw(node=node_to_upgrade, command=["systemctl", "start", "libvirtd-tls.socket"])
        run_one_raw(node=node_to_upgrade, command=["systemctl", "start", "libvirtd"])
        run_one_raw(node=node_to_upgrade, command=["run-puppet-agent"])
        run_one_raw(node=node_to_upgrade, command=["systemctl", "restart", "nova-compute"])
        run_one_raw(node=node_to_upgrade, command=["journalctl", "-n", "500"])
        LOGGER.info(
            "Those were the last lines of the journal, make sure everything looks ok before upgrading the next host."
        )
        LOGGER.info("%s Done!!! \\o/", self.fqdn_to_upgrade)
