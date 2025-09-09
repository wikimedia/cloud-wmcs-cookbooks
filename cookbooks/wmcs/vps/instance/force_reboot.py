r"""WMCS vps - force reboot an instance

Usage example: wmcs.vps.instance.force_reboot \
    --vm-name fullstack-20220613230939 \
    --project admin-monitoring

"""

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class ForceReboot(CookbookBase):
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
            help="Openstack cluster_name where the VM is hosted, if using --vm-fqdn, it will be ignored.",
        )
        parser.add_argument(
            "--vm-name",
            required=False,
            help="Name of the virtual machine (usually the hostname).",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_common_opts(self.spicerack, args, ForceRebootRunner)(
            cluster_name=args.cluster_name,
            vm_name=args.vm_name,
            spicerack=self.spicerack,
        )


class ForceRebootRunner(WMCSCookbookRunnerBase):

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
        return f"vm {self.vm_name} (cluster {self.cluster_name}, project {self.project})"

    def run_with_proxy(self) -> None:
        self.openstack_api.server_force_reboot(name_to_reboot=self.vm_name)
