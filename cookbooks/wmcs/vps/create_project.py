r"""WMCS VPS - Create a new project

Usage example:
    cookbook wmcs.vps.create_project \
        --cluster-name eqiad1 \
        --project my_fancy_new_project

"""

from __future__ import annotations

import argparse
import logging
from typing import Any

import gitlab
import yaml
from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation, ask_input

from cookbooks.wmcs.openstack.tofu import OpenstackTofuRunner
from cookbooks.wmcs.vps.add_user_to_project import AddUserToProjectRunner
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI, OpenstackQuotaEntry, OpenstackQuotaName
from wmcs_libs.wm_gitlab import GitlabController

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
        parser.add_argument(
            "--skip-mr",
            action="store_true",
            help="If set, it will not send a merge request to tofu." " Useful if you already merged the MR manually.",
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
        quotas = [
            OpenstackQuotaEntry.from_human_spec(
                name=quota_name,
                human_spec=getattr(args, quota_name.value.replace("-", "_")),
            )
            for quota_name in OpenstackQuotaName
            if getattr(args, quota_name.value.replace("-", "_"), None) is not None
        ]
        for quota in quotas:
            if quota.name == OpenstackQuotaName.RAM and quota.value < 1024:
                ask_confirmation(
                    "Are you sure you want to set the ram with less than 1G? (got "
                    f"{quota}M, maybe you forgot to add 'G' to the value?)"
                )

        return with_common_opts(
            self.spicerack,
            args,
            CreateProjectRunner,
        )(
            description=args.description,
            cluster_name=args.cluster_name,
            trove_only=args.trove_only,
            users=args.users,
            skip_mr=args.skip_mr,
            quotas=quotas,
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
        skip_mr: bool,
        quotas: list[OpenstackQuotaEntry],
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
        self.quotas = quotas
        self.skip_mr = skip_mr

        self.common_opts = common_opts
        super().__init__(spicerack=spicerack, common_opts=common_opts)

        self.gitlab_controller = GitlabController(private_token=self.wmcs_config.get("gitlab_token", None))

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        trove_only = "trove-only " if self.trove_only else ""
        return f"for {trove_only}project {self.common_opts.project} in {self.openstack_api.cluster_name.value}"

    def _do_preflight_checks(self) -> None:
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

    def _create_trove_project(self) -> None:
        self.openstack_api.quota_set(
            OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.INSTANCES, human_spec="0")
        )
        self.openstack_api.quota_set(OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.CORES, human_spec="0"))
        self.openstack_api.quota_set(OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.RAM, human_spec="0"))
        self.openstack_api.quota_set(
            OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.GIGABYTES, human_spec="0")
        )
        self.openstack_api.quota_set(
            OpenstackQuotaEntry.from_human_spec(name=OpenstackQuotaName.VOLUMES, human_spec="0")
        )
        # confusingly, 'volumes' here refers to GB of database storage. It defaults to '2' so we need
        # to increase it for trove-only projects.
        self.openstack_api.trove_quota_set("volumes", "80")

    def _get_tofu_project_data(self, task_id: str | None) -> dict[str, Any]:
        project_data = {
            "description": self.description,
        }

        if task_id:
            project_data["task_id"] = task_id

        return project_data

    def _create_tofu_mr(self) -> gitlab.v4.objects.merge_requests.ProjectMergeRequest:
        branch_name = f"add_project_{self.common_opts.project}"
        projects_file = f"projects_{self.openstack_api.cluster_name}.yaml"
        projects_content = self.gitlab_controller.get_file_at_commit(
            project="tofu-infra", file_path=projects_file, commit_sha="main"
        )
        projects_data = yaml.safe_load(projects_content)
        projects_data[self.common_opts.project] = self._get_tofu_project_data(task_id=self.common_opts.task_id)

        title = f"projects: added project {self.common_opts.project}"
        self.gitlab_controller.update_file(
            project="tofu-infra",
            new_branch=branch_name,
            file_path=projects_file,
            new_content=yaml.safe_dump(projects_data),
            commit_message=(
                f"{title}\nAutomatic commit by cookbook wmcs.vps.create_project\n\nBug: "
                f"{self.common_opts.task_id or 'no task'}"
            ),
            author_email="donotreply@cookbook.wmcs.local",
            author_name="Cookbook",
        )
        mr = self.gitlab_controller.create_mr(
            project="tofu-infra",
            source_branch=branch_name,
            target_branch="main",
            title=title,
        )
        return mr

    def _is_mr_merged(self, mr_iid: str) -> bool:
        mr = self.gitlab_controller.get_mr(project="tofu-infra", mr_iid=mr_iid)
        if mr.state != "merged":
            LOGGER.error("The MR is not yet merged! It's %s", mr.state)
            return False

        return True

    def _wait_for_merged_loop(self, change_mr: gitlab.v4.objects.merge_requests.ProjectMergeRequest) -> None:
        is_merged = False
        while not is_merged:
            response = ask_input(
                message=(
                    "We track project lifecycle now via opentofu.\n"
                    f"I created the merge request {change_mr.web_url} and ran tofu plan on it, "
                    "get it reviewed and merged before continuing\n\n"
                    "See: https://wikitech.wikimedia.org/wiki/Portal:Cloud_VPS/Admin/Projects_lifecycle#Creating_a_new_project\n"  # noqa: E501
                    "\n"
                    "Enter go when the patch is merged, plan to rerun the tofu plan, or abort to exit:"
                ),
                choices=["abort", "go", "plan"],
            )

            if response == "go":
                break

            if response == "abort":
                raise Exception("Aborted at user request.")

            if response == "plan":
                OpenstackTofuRunner(
                    common_opts=self.common_opts,
                    plan=True,
                    apply=False,
                    gitlab_mr=change_mr.iid,
                    no_gitlab_mr_note=False,
                    spicerack=self.spicerack,
                ).run()

            else:
                is_merged = self._is_mr_merged(mr_iid=change_mr.mr_iid)

    def run(self) -> None:
        """Main entry point"""

        self._do_preflight_checks()

        if not self.skip_mr:
            change_mr = self._create_tofu_mr()

            OpenstackTofuRunner(
                common_opts=self.common_opts,
                plan=True,
                apply=False,
                gitlab_mr=change_mr.iid,
                no_gitlab_mr_note=False,
                spicerack=self.spicerack,
            ).run()

            self._wait_for_merged_loop(change_mr=change_mr)

        OpenstackTofuRunner(
            common_opts=self.common_opts,
            plan=True,
            apply=True,
            spicerack=self.spicerack,
        ).run()

        # NOTE! change to the newly created project
        self.openstack_api.project = self.common_opts.project

        if self.trove_only:
            self._create_trove_project()

        if self.quotas:
            LOGGER.info("Setting quotas")
            self.openstack_api.quota_set(*self.quotas)

        if self.users:
            LOGGER.info("Adding users %r", self.users)
            for user in self.users:
                AddUserToProjectRunner(
                    common_opts=self.common_opts,
                    user=user,
                    as_member=True,
                    cluster_name=self.openstack_api.cluster_name,
                    spicerack=self.spicerack,
                ).run()

        LOGGER.info("The project %s has been created.", self.common_opts.project)
        if self.users:
            LOGGER.info("  NOTE: make sure that the users ar instructed to join the cloud-announce mailing list.")
