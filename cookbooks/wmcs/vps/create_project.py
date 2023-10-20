r"""WMCS VPS - Create a new project

Usage example:
    cookbook wmcs.vps.create_project \
        --cluster-name eqiad1 \
        --project my_fancy_new_project

"""
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI, OpenstackQuotaEntry, OpenstackQuotaName

LOGGER = logging.getLogger(__name__)


class CreateProject(CookbookBase):
    """WMCS VPS cookbook to add a user to a project."""

    title = __doc__

    def argument_parser(self) -> argparse.ArgumentParser:
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=False,
            choices=list(OpenstackClusterName),
            default=OpenstackClusterName.EQIAD1,
            type=OpenstackClusterName,
            help="Openstack cluster name to use.",
        )
        # Hack around having the project flag created with add_common_opts
        project_action = next(
            action for action in parser._actions if action.dest == "project"  # pylint: disable=protected-access
        )
        project_action.help = "Name of the project to create."
        project_action.default = None
        project_action.required = True
        parser.add_argument(
            "--description",
            required=True,
            type=str,
            help="Description for the new CloudVps project",
        )
        parser.add_argument(
            "--trove-only",
            action="store_true",
            help="If set, the new project will have quotas that prevent "
            "creation of VMs or volumes and elevated DB quotas.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, CreateProjectRunner,)(
            description=args.description,
            cluster_name=args.cluster_name,
            trove_only=args.trove_only,
            spicerack=self.spicerack,
        )


class CreateProjectRunner(WMCSCookbookRunnerBase):
    """Runner for CreateProject."""

    def __init__(
        self,
        common_opts: CommonOpts,
        description: str,
        trove_only: bool,
        cluster_name: OpenstackClusterName,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=cluster_name,
        )
        self.description = description
        self.trove_only = trove_only

        self.common_opts = common_opts
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        trove_only = "trove-only " if self.trove_only else ""
        return f"for {trove_only}project {self.common_opts.project} in {self.openstack_api.cluster_name.value}"

    def run(self) -> None:
        """Main entry point"""
        if "_" in self.common_opts.project:
            ask_confirmation(
                "An underscore is generally not a valid character in a DNS label, "
                "are you sure you want to use one in a project name?"
            )
        if "-" in self.common_opts.project:
            ask_confirmation(
                "Using a dash in a project name will cause issues with Ceph RGW (Swift/S3), "
                "are you sure you want to use this project name?"
            )

        self.openstack_api.project_create(project=self.common_opts.project, description=self.description)
        if self.trove_only:
            self.openstack_api.quota_set(
                OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.INSTANCES, human_spec="0")
            )
            self.openstack_api.quota_set(
                OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.CORES, human_spec="0")
            )
            self.openstack_api.quota_set(
                OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.RAM, human_spec="0")
            )
            self.openstack_api.quota_set(
                OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.GIGABYTES, human_spec="0")
            )
            self.openstack_api.quota_set(
                OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.VOLUMES, human_spec="0")
            )
            # confusingly, 'volumes' here refers to GB of database storage. It defaults to '2' so we need
            # to increase it for trove-only projects.
            self.openstack_api.trove_quota_set("volumes", "80")
