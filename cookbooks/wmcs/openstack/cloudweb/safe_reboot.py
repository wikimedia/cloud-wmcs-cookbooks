r"""WMCS openstack - Safely reboot cloudweb nodes.

Usage example: wmcs.openstack.cloudweb.safe_reboot \
    --cluster-name eqiad1

"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from spicerack import RemoteHosts

from wmcs_libs.common import WMCSCookbookRunnerBase, with_common_opts
from wmcs_libs.openstack.batch import CloudwebBatchBase, CloudwebBatchRunnerBase

LOGGER = logging.getLogger(__name__)


class SafeReboot(CloudwebBatchBase):
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


class SafeRebootRunner(CloudwebBatchRunnerBase):

    downtime_reason = "host reboot"

    def run_on_hosts(self, hosts: RemoteHosts) -> None:
        reboot_time = datetime.utcnow()

        LOGGER.info("Rebooting %s", hosts)

        hosts.reboot()
        hosts.wait_reboot_since(reboot_time)
