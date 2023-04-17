r"""WMCS openstack - increase a project's quota by a given amount

If talking about memory, things like 10G/250M are supported.

Usage example: wmcs.openstack.quota_increase \
    --project admin-monitoring \
    --gigabytes 30G \
    --cluster-name eqiad1 \
    --instances 5

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation

from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI, OpenstackQuotaEntry, OpenstackQuotaName

LOGGER = logging.getLogger(__name__)


class QuotaIncrease(CookbookBase):
    """WMCS Openstack cookbook to increase the quota of a project."""

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
            "--cluster-name",
            required=True,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="Openstack cluster/deployment to act on.",
        )
        for quota_name in OpenstackQuotaName:
            parser.add_argument(
                f"--{quota_name.value}",
                required=False,
                help=f"Amount to increase the {quota_name.value} by",
            )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        if args.project == "admin":
            ask_confirmation("Are you sure you want to increase the quota of the admin project?")

        increases = [
            OpenstackQuotaEntry.from_human_spec(
                name=quota_name,
                human_spec=getattr(args, quota_name.value.replace("-", "_")),
            )
            for quota_name in OpenstackQuotaName
            if getattr(args, quota_name.value.replace("-", "_"), None) is not None
        ]
        return with_common_opts(spicerack=self.spicerack, args=args, runner=QuotaIncreaseRunner)(
            increases=increases,
            spicerack=self.spicerack,
            cluster_name=args.cluster_name,
        )


class QuotaIncreaseRunner(WMCSCookbookRunnerBase):
    """Runner for QuotaIncrease"""

    def __init__(
        self,
        common_opts: CommonOpts,
        increases: list[OpenstackQuotaEntry],
        cluster_name: OpenstackClusterName,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        super().__init__(spicerack=spicerack)
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(), cluster_name=cluster_name, project=self.common_opts.project
        )
        self.increases = increases
        self.sallogger = SALLogger.from_common_opts(self.common_opts)

    def run(self) -> None:
        """Main entry point"""
        if not self.increases:
            print("Nothing to increase, did you forget to pass any options?")
            return

        self.openstack_api.quota_increase(*self.increases)
        self.sallogger.log(f"Increased quotas by {', '.join(str(increase) for increase in self.increases)}")
