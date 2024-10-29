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
from wmcs_libs.inventory.openstack import OpenstackClusterName
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
            "--user",
            required=False,
            action="append",
            dest="users",
            default=[],
            help="Users to add as maintainers to the project.",
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
        return with_common_opts(
            self.spicerack,
            args,
            CreateProjectRunner,
        )(
            description=args.description,
            cluster_name=args.cluster_name,
            trove_only=args.trove_only,
            users=args.users,
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
        users: list[str],
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        self.common_opts = common_opts
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=cluster_name,
        )
        self.description = description
        self.trove_only = trove_only
        self.users = users

        self.common_opts = common_opts
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        trove_only = "trove-only " if self.trove_only else ""
        return f"for {trove_only}project {self.common_opts.project} in {self.openstack_api.cluster_name.value}"

    def run(self) -> None:
        """Main entry point"""
        # Checks mentioned in:
        # https://wikitech.wikimedia.org/wiki/Portal:Cloud_VPS/Admin/Projects_lifecycle#Creating_a_new_project
        LOGGER.info("Doing some pre-flight checks...")

        if "_" in self.common_opts.project:
            ask_confirmation(
                "Project names should generally be limited to alphanumeric characters and dashes, otherwise they "
                "might have issues with DNS. Are you sure you want to use an underscore in the name? "
                f"(current name {self.common_opts.project})"
            )

        if self.common_opts.project.lower() != self.common_opts.project:
            ask_confirmation(
                "Project names should be lowercase, otherwise they have issues with puppet certificates. "
                "Are you sure you want to use an underscore in the name? "
                f"(current name {self.common_opts.project})"
            )

        recordsets = self.openstack_api.get_vm_proxy_recordsets()
        for recordset_data in recordsets:
            if recordset_data["name"].lower() == f"{self.common_opts.project.lower()}.wmcloud.org.":
                message = (
                    f"There's already an old recordset matching the name '{recordset_data['name']}', maybe a "
                    "proxy with that name already exists? See T360294. Aborting."
                )
                LOGGER.error(message)
                raise Exception(message)

        all_users_data = self.openstack_api.get_all_users()
        # Openstack allows passing the user id or the user name, it does not care
        user_names_and_ids = set()
        for user_data in all_users_data:
            user_names_and_ids.add(user_data["ID"])
            user_names_and_ids.add(user_data["Name"])

        missed_users: list[str] = []
        for user in self.users:
            if user not in user_names_and_ids:
                missed_users.append(user)

        if missed_users:
            message = f"Unable to find users {missed_users} in LDAP, can you double check the spelling?"
            LOGGER.error(message)
            raise Exception(message)

        ask_confirmation(
            "We track project lifecycle now via opentofu. This cookbook can't handle it yet, so you have to send a patch, merge and run tofu to apply.\n"  # noqa: E501
            "See: https://wikitech.wikimedia.org/wiki/Portal:Cloud_VPS/Admin/Projects_lifecycle#Creating_a_new_project\n"  # noqa: E501
            "Enter go when the patch is merged:"
        )
        # change to the newly created project
        self.openstack_api.project = self.common_opts.project
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
