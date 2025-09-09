r"""WMCS Openstack - Reboot a cloudcontrol node .

Usage example:
    cookbook wmcs.openstack.cloudcontrol.reboot_node \
        --fqdn cloudcontrol1011.eqiad.wmnet

"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from spicerack import RemoteHosts

from wmcs_libs.common import WMCSCookbookRunnerBase, with_common_opts
from wmcs_libs.openstack.batch import CloudcontrolBatchBase, CloudcontrolBatchRunnerBase

LOGGER = logging.getLogger(__name__)


class RebootNode(CloudcontrolBatchBase):
    __doc__ = __doc__

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(
            self.spicerack,
            args,
            RebootNodeRunner,
        )(
            args=args,
            spicerack=self.spicerack,
        )


class RebootNodeRunner(CloudcontrolBatchRunnerBase):

    downtime_reason = "host reboot"

    def run_on_hosts(self, hosts: RemoteHosts) -> None:

        reboot_time = datetime.utcnow()
        hosts.reboot()
        hosts.wait_reboot_since(since=reboot_time)
