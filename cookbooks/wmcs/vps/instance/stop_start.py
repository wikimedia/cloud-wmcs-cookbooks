r"""WMCS Cloud VPS - stop and start an instance

Usage example: wmcs.vps.instance.stop_start \
    --vm-name fullstack-20220613230939 \
    --project admin-monitoring

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class StopStart(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):
        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=False,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            default=OpenstackClusterName.EQIAD1,
            help="Openstack cluster name where the VM is hosted.",
        )
        parser.add_argument(
            "--vm-name",
            required=False,
            help="Name of the virtual machine (usually the hostname).",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_common_opts(self.spicerack, args, StopStartRunner)(
            cluster_name=args.cluster_name,
            vm_name=args.vm_name,
            spicerack=self.spicerack,
        )


class StopStartRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: OpenstackClusterName,
        vm_name: str,
        spicerack: Spicerack,
    ):
        self.project = common_opts.project
        self.vm_name = vm_name
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.openstack_api = OpenstackAPI(remote=spicerack.remote(), cluster_name=cluster_name, project=self.project)

    @property
    def runtime_description(self):
        return f"vm {self.vm_name} (cluster {self.cluster_name})"

    def run_with_proxy(self) -> None:
        self.openstack_api.server_stop(self.vm_name)
        self.openstack_api.server_start(self.vm_name)
