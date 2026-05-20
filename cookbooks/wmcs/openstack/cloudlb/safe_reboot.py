r"""WMCS openstack - Safely reboot a cloudlb node.

Usage example: wmcs.openstack.cloudlb.safe_reboot \
    --cluster-name codfw1dev

"""

import argparse
from datetime import datetime, timezone

from spicerack import RemoteHosts
from wmflib.interactive import confirm_on_failure

from wmcs_libs.bird import Bird
from wmcs_libs.common import WMCSCookbookRunnerBase, with_common_opts
from wmcs_libs.openstack.batch import CloudlbBatchBase, CloudlbBatchRunnerBase


class SafeReboot(CloudlbBatchBase):
    __doc__ = __doc__

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_common_opts(
            self.spicerack,
            args,
            SafeRebootRunner,
        )(
            args=args,
            spicerack=self.spicerack,
        )


class SafeRebootRunner(CloudlbBatchRunnerBase):
    downtime_reason = "host reboot"

    def run_on_hosts(self, hosts: RemoteHosts) -> None:
        if len(hosts) != 1:
            raise ValueError("safe_reboot does not support on operating on multiple nodes at once")

        reboot_time = datetime.now(timezone.utc)
        hosts.reboot()
        hosts.wait_reboot_since(reboot_time)

        bird = Bird(hosts)
        confirm_on_failure(bird.ensure_bgp_established)
