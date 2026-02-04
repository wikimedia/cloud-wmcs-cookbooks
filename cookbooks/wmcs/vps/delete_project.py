r"""WMCS VPS - Delete a project

Usage example:
    cookbook wmcs.vps.delete_project \
        --cluster-name eqiad1 \
        --project useless-project

"""

from __future__ import annotations

import argparse
import logging
import re

import gitlab
from spicerack import Spicerack
from spicerack.cookbook import CookbookBase
from wmflib.interactive import ask_input, confirm_on_failure

from cookbooks.wmcs.openstack.tofu import OpenstackTofuRunner
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI
from wmcs_libs.wm_gitlab import GitlabController

LOGGER = logging.getLogger(__name__)


class DeleteProject(CookbookBase):
    """WMCS VPS cookbook to delete a project."""

    title = __doc__

    def argument_parser(self) -> argparse.ArgumentParser:
        """Parse the command line arguments for this cookbook."""
        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=False,
            choices=list(OpenstackClusterName),
            default=OpenstackClusterName.EQIAD1,
            type=OpenstackClusterName,
            help="Openstack cluster name to use.",
        )
        parser.add_argument(
            "--skip-mr",
            action="store_true",
            help="If set, it will not send a merge request to tofu. Useful if you already merged the MR manually.",
        )

        # Hack around having the project flag created with add_common_opts
        project_action = next(
            action for action in parser._actions if action.dest == "project"  # pylint: disable=protected-access
        )
        project_action.help = "Name of the project to delete."
        project_action.default = None
        project_action.required = True

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            DeleteProjectRunner,
        )(
            cluster_name=args.cluster_name,
            skip_mr=args.skip_mr,
            spicerack=self.spicerack,
        )


class DeleteProjectRunner(WMCSCookbookRunnerBase):
    """Runner for DeleteProject."""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: OpenstackClusterName,
        skip_mr: bool,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=cluster_name,
        )
        # Make sure we are consistent that the argument is the project name,
        # and OpenStack calls use the project ID.
        # (Note _resolve_project_id uses the project-less self.openstack_api object!)
        self.openstack_api.project = self._resolve_project_id()

        self.skip_mr = skip_mr

        self.common_opts = common_opts
        super().__init__(spicerack=spicerack, common_opts=common_opts)

        self.gitlab_controller = GitlabController(private_token=self.wmcs_config.get("gitlab_token", None))

    def _resolve_project_id(self) -> str:
        projects = [
            project for project in self.openstack_api.get_all_projects() if project["Name"] == self.common_opts.project
        ]
        if not projects:
            # Already deleted!
            raise Exception(f"Project {self.common_opts.project} not found!")

        return projects[0]["ID"]

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for project {self.common_opts.project} in {self.openstack_api.cluster_name.value}"

    def _do_preflight_checks(self) -> None:
        # Checks mentioned in:
        # https://wikitech.wikimedia.org/wiki/Portal:Cloud_VPS/Admin/Projects_lifecycle#Creating_a_new_project
        LOGGER.info("Doing some pre-flight checks...")

        instances = self.openstack_api.server_list()
        if instances:
            message = f"The following instances still exist: {', '.join(instance['Name'] for instance in instances)}"
            LOGGER.error(message)
            raise Exception(message)

    def _cleanup_dns(self) -> None:
        zones = self.openstack_api.zone_list()
        for zone in zones:
            LOGGER.info("Deleting DNS zone %s (%s)", zone.name, zone.zone_id)
            self.openstack_api.zone_delete(zone.zone_id)

    def _create_tofu_mr(self) -> gitlab.v4.objects.merge_requests.ProjectMergeRequest:
        branch_name = f"delete_project_{self.common_opts.project}"
        mr_title = f"projects: delete project {self.common_opts.project}"

        delete_project_dir_change = {
            "action": "delete",
            "file_path": f"resources/{self.openstack_api.cluster_name}-r/{self.common_opts.project}",
        }

        projects_main_tf = f"resources/{self.openstack_api.cluster_name}-r/main.tf"
        projects_main_tf_content = self.gitlab_controller.get_file_at_commit(
            project="tofu-infra",
            file_path=projects_main_tf,
            commit_sha="main",
        )
        projects_main_tf_content = projects_main_tf_content.replace(
            f"""
module "project_{self.common_opts.project}" {{
  source = "./{self.common_opts.project}/"
}}
""".rstrip(),
            "",
        )
        projects_main_tf_content = re.sub(
            rf" +{self.common_opts.project} += module\.project_{self.common_opts.project}\.resources,\n",
            "",
            projects_main_tf_content,
        )
        projects_main_tf_file_change = {
            "action": "update",
            "file_path": projects_main_tf,
            "content": projects_main_tf_content,
        }

        self.gitlab_controller.create_commit(
            project="tofu-infra",
            new_branch=branch_name,
            actions=[projects_main_tf_file_change, delete_project_dir_change],
            commit_message=(
                f"{mr_title}\n\nAutomatic commit by cookbook wmcs.vps.create_project\n\nBug: "
                f"{self.common_opts.task_id or 'no task'}"
            ),
            author_email="donotreply@cookbook.wmcs.local",
            author_name="Cookbook",
        )
        mr = self.gitlab_controller.create_mr(
            project="tofu-infra",
            source_branch=branch_name,
            target_branch="main",
            title=mr_title,
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
                confirm_on_failure(
                    OpenstackTofuRunner(
                        common_opts=self.common_opts,
                        plan=True,
                        apply=False,
                        gitlab_mr=change_mr.iid,
                        no_gitlab_mr_note=False,
                        cluster_name=self.cluster_name,
                        spicerack=self.spicerack,
                    ).run
                )

            else:
                is_merged = self._is_mr_merged(mr_iid=change_mr.mr_iid)

    def _delete_via_tofu(self):
        if not self.skip_mr:
            change_mr = self._create_tofu_mr()

            confirm_on_failure(
                OpenstackTofuRunner(
                    common_opts=self.common_opts,
                    plan=True,
                    apply=False,
                    gitlab_mr=change_mr.iid,
                    no_gitlab_mr_note=False,
                    cluster_name=self.cluster_name,
                    spicerack=self.spicerack,
                ).run
            )

            self._wait_for_merged_loop(change_mr=change_mr)

        confirm_on_failure(
            OpenstackTofuRunner(
                common_opts=self.common_opts,
                plan=True,
                apply=True,
                cluster_name=self.cluster_name,
                spicerack=self.spicerack,
            ).run
        )

    def _delete_unmanaged_project(self):
        """Delete a project via the openstack CLI, if it was not already deleted by tofu."""
        projects = [
            project
            for project in self.openstack_api.get_all_projects()
            if project["Name"] == self.openstack_api.project
        ]
        if not projects:
            # Already deleted!
            return

        LOGGER.info("Deleting project via OpenStack CLI")
        self.openstack_api.project_delete(projects[0]["ID"])

    def run(self) -> None:
        """Main entry point"""

        self._do_preflight_checks()

        self._cleanup_dns()

        self._delete_via_tofu()

        # Project might not exist at this point
        self.openstack_api.project = ""
        self._delete_unmanaged_project()
