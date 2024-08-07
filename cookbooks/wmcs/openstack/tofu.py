r"""WMCS Openstack opentofu - run opentofu for Cloud VPS openstack

Usage examples:
    cookbook wmcs.openstack.tofu --apply
    cookbook wmcs.openstack.tofu --gitlab-mr 123
    cookbook wmcs.openstack.tofu --gitlab-mr 123 --cluster-name codfw1dev --plan
"""

from __future__ import annotations

import argparse
import logging
from contextlib import contextmanager
from typing import Any

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation

from wmcs_libs.common import (
    CommonOpts,
    CuminParams,
    WMCSCookbookRunnerBase,
    add_common_opts,
    run_one_raw,
    with_common_opts,
    with_temporary_file,
)
from wmcs_libs.gitlab import GitlabController
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.clusters import get_openstack_clusters
from wmcs_libs.openstack.common import get_control_nodes

LOGGER = logging.getLogger(__name__)


class OpenstackTofu(CookbookBase):
    """Run opentofu for Cloud VPS openstack"""

    title = __doc__

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
            required=False,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="Openstack deployment to act on. Otherwise, the cookbook will run against all deployments.",
        )
        parser.add_argument(
            "--plan",
            required=False,
            default=True,
            action="store_true",
            help="run opentofu plan",
        )
        parser.add_argument(
            "--apply",
            required=False,
            default=False,
            action="store_true",
            help="run opentofu apply",
        )
        parser.add_argument(
            "--gitlab-mr",
            required=False,
            type=int,
            help="gitlab merge request number",
        )
        parser.add_argument(
            "--no-gitlab-mr-note",
            required=False,
            default=False,
            action="store_true",
            help="if running for a gitlab MR, and if specified, don't write a note to the MR",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, OpenstackTofuRunner)(
            plan=args.plan,
            apply=args.apply,
            gitlab_mr=args.gitlab_mr,
            cluster_name=args.cluster_name,
            no_gitlab_mr_note=args.no_gitlab_mr_note,
            spicerack=self.spicerack,
        )


def _concat(commands: list[str]) -> str:
    return " && ".join(commands)


def _sh_wrap(cmd: str) -> list[str]:
    return ["/bin/sh", "-c", "--", f"'{cmd}'"]


cumin_params = CuminParams(print_progress_bars=False)


class OpenstackTofuRunner(WMCSCookbookRunnerBase):
    """Runner for OpenstackTofu"""

    GITLAB_BASE_URL = "https://gitlab.wikimedia.org"
    GITLAB_REPO_NAME = "tofu-infra"
    GITLAB_REPO_URL = f"{GITLAB_BASE_URL}/repos/cloud/cloud-vps/{GITLAB_REPO_NAME}"
    TOFU_INFRA_DIR = "/srv/tofu-infra/"

    def __init__(
        self,
        common_opts: CommonOpts,
        plan: bool,
        apply: bool,
        gitlab_mr: int,
        cluster_name: OpenstackClusterName,
        no_gitlab_mr_note: bool,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.common_opts = common_opts
        self.plan = plan
        self.apply = apply
        self.gitlab_mr = gitlab_mr
        self.cluster_name = cluster_name

        if self.gitlab_mr and self.apply:
            raise Exception("You cannot run 'apply' for a merge request. Merge it then apply main.")

        if self.apply and self.cluster_name:
            raise Exception("You can only run 'apply' for all clusters, i.e: don't specify --cluster_name")

        self.gitlab_controller = None
        if self.gitlab_mr and not no_gitlab_mr_note:
            private_token = self.wmcs_config.get("gitlab_token", None)
            self.gitlab_controller = GitlabController(private_token=private_token)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        apply = "+apply" if self.apply else ""
        branch = (
            f"for {self.GITLAB_REPO_URL}/-/merge_requests/{self.gitlab_mr}" if self.gitlab_mr else "for main branch"
        )
        return f"running tofu plan{apply} {branch}"

    @staticmethod
    def _exec(node: Any, commands: list[str]) -> None:
        _command = _sh_wrap(_concat(commands))
        LOGGER.info("INFO: running command: %s", _command)
        try:
            run_one_raw(node=node, command=_command, cumin_params=cumin_params)
        except Exception as e:
            error_msg = f"ERROR: failed to run command: '{_command}': '{e}'"
            LOGGER.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _git_cleanup_and_checkout_main_branch(self, node: Any) -> None:
        main_branch = "main"
        commands = [
            f"cd {self.TOFU_INFRA_DIR}",
            # flush any local changes first
            "git checkout -f",
            "git clean -fd",
            # checkout main branch
            f"git checkout {main_branch}",
            "git pull --rebase",
        ]
        self._exec(node, commands)

    def _git_checkout_mr(self, node: Any) -> None:
        # see https://gitlab.wikimedia.org/help/user/project/merge_requests/merge_request_troubleshooting
        remote = "origin"
        commands = [
            f"cd {self.TOFU_INFRA_DIR}",
            # force ignores errors due to diverging mr branches (case if the MR is updated and the cookbook rerun)
            f"git fetch --force {remote} merge-requests/{self.gitlab_mr}/head:mr-{remote}-{self.gitlab_mr}",
            f"git checkout --force mr-{remote}-{self.gitlab_mr}",
        ]
        self._exec(node, commands)

    def _tofu_plan(self, node: Any, plan_file: str) -> None:
        commands = [
            f"cd {self.TOFU_INFRA_DIR}",
            "tofu init",
            "tofu validate",
            f"tofu plan -out={plan_file}",
        ]
        self._exec(node, commands)

    def _tofu_apply(self, node: Any, plan_file: str) -> None:
        commands = [
            f"cd {self.TOFU_INFRA_DIR}",
            f"tofu apply {plan_file}",
        ]
        self._exec(node, commands)

    def _tofu_plan_to_gitlab_note(self, node: Any, cluster_name: str, plan_file: str) -> None:
        if not self.gitlab_controller:
            return

        commands = [
            f"cd {self.TOFU_INFRA_DIR}",
            f"tofu show -no-color {plan_file}",
        ]
        tofu_show_cumin_params = CuminParams(print_progress_bars=False, print_output=False, is_safe=True)
        try:
            plan = run_one_raw(node=node, command=_sh_wrap(_concat(commands)), cumin_params=tofu_show_cumin_params)
        except Exception as e:  # pylint: disable=broad-except
            LOGGER.warning("WARNING: unable to get content of the tofu plan: %s", str(e))
            return

        if not plan:
            LOGGER.warning("WARNING: the tofu plan is empty, not writing note to the MR")
            return

        try:
            project_id = self.gitlab_controller.get_project_id_by_name(self.GITLAB_REPO_NAME)
        except Exception as e:  # pylint: disable=broad-except
            LOGGER.warning("WARNING: unable to write gitlab note to merge request: %s", str(e))
            return

        LOGGER.info("INFO: writing note with tofu plan to gitlab merge request")

        # TODO: apparently, code blocks aren't created correctly inside <details> blocks :-(
        note_body = (
            f"tofu plan was run for this merge request in cluster `{cluster_name}`:\n"
            "<details>\n"
            "<summary>Click to expand tofu plan</summary>\n"
            f"{plan}\n"
            "</details>\n"
        )
        self.gitlab_controller.create_mr_note(
            project_id=project_id, merge_request_iid=self.gitlab_mr, note_body=note_body
        )

    @contextmanager
    def _with_merge_request(self, node: Any) -> Any:
        """Checkout the gitlab merge request, then rollback the local git repository to the main branch."""
        try:
            if self.gitlab_mr:
                LOGGER.info("INFO: checking out gitlab MR branch")
                self._git_checkout_mr(node)
            yield
        finally:
            if self.gitlab_mr:
                LOGGER.info("INFO: cleaning up git repository tree back to the main branch")
                self._git_cleanup_and_checkout_main_branch(node)

    def _run(self, cluster_name: str) -> None:
        """Run the routine"""

        control_node_fqdn = get_control_nodes(cluster_name=OpenstackClusterName(cluster_name))[0]

        LOGGER.info("INFO: running tofu for deployment '%s', node '%s", cluster_name, control_node_fqdn)

        query = f"P{{{control_node_fqdn}}}"
        node = self.spicerack.remote().query(query, use_sudo=True)

        self._git_cleanup_and_checkout_main_branch(node)

        with with_temporary_file(dst_node=node, contents="", cumin_params=cumin_params) as plan_file:
            with self._with_merge_request(node):
                self._tofu_plan(node, plan_file=plan_file)

            self._tofu_plan_to_gitlab_note(node=node, cluster_name=cluster_name, plan_file=plan_file)

            run_apply = False
            if self.apply:
                try:
                    ask_confirmation(f"Before apply, Is tofu plan correct ({cluster_name} @ {control_node_fqdn})?")
                    run_apply = True
                except Exception:  # pylint: disable=broad-except:
                    LOGGER.warning("WARNING: not running tofu apply because plan was not accepted")

            if run_apply:
                self._tofu_apply(node, plan_file=plan_file)

    def run_with_proxy(self) -> None:
        """Main entry point"""

        clusters = get_openstack_clusters()
        for cluster_name in clusters:
            if self.cluster_name and cluster_name != self.cluster_name:
                # user wanted to filter operations for a given deployment
                continue

            self._run(cluster_name=str(cluster_name))
