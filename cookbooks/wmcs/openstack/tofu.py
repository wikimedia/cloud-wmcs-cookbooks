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
from pathlib import Path
from typing import Any, Generator

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation

from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_PROGRESS,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_PROGRESS,
    CommonOpts,
    WMCSCookbookRunnerBase,
    add_common_opts,
    run_one_raw,
    run_script,
    with_common_opts,
    with_temporary_dir,
    with_temporary_file,
)
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.clusters import get_openstack_clusters
from wmcs_libs.openstack.common import get_control_nodes
from wmcs_libs.wm_gitlab import GitlabController

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


class OpenstackTofuRunner(WMCSCookbookRunnerBase):
    """Runner for OpenstackTofu"""

    GITLAB_BASE_URL = "https://gitlab.wikimedia.org"
    GITLAB_REPO_NAME = "tofu-infra"
    GITLAB_REPO_URL = f"{GITLAB_BASE_URL}/repos/cloud/cloud-vps/{GITLAB_REPO_NAME}"
    # long tofu plans in gitlab notes without collapse are annoying. But also, markdown formatting inside
    # gitlab notes is broken, so we don't want to collapse _all_ tofu plan, only those with line count
    # above this arbitrary threshold
    GITLAB_MR_NOTE_COLLAPSE_AT_LINES_THRESHOLD = 500
    TOFU_INFRA_ORIG_DIR = Path("/srv/tofu-infra")
    TOFU_NO_CHANGES_LINE = "No changes. Your infrastructure matches the configuration."

    def __init__(
        self,
        common_opts: CommonOpts,
        plan: bool,
        apply: bool,
        spicerack: Spicerack,
        no_gitlab_mr_note: bool = True,
        gitlab_mr: int | None = None,
        cluster_name: OpenstackClusterName | None = None,
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

    def _git_cleanup_and_checkout_main_branch(self, node: Any) -> None:
        main_branch = "main"
        script = f"""
cd '{self.TOFU_INFRA_ORIG_DIR}'
# flush any local changes first
git checkout -f
git clean -fd
# checkout main branch
git checkout '{main_branch}'
git pull --rebase
"""
        run_script(node=node, script=script, cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

    def _git_checkout_mr(self, node: Any, op_dir: Path) -> None:
        # see https://gitlab.wikimedia.org/help/user/project/merge_requests/merge_request_troubleshooting
        remote = "origin"
        script = f"""
cp -a {self.TOFU_INFRA_ORIG_DIR}/. {op_dir}/
cd {op_dir}
# force ignores errors due to diverging mr branches (case if the MR is updated and the cookbook rerun)
git fetch --force '{remote}' 'merge-requests/{self.gitlab_mr}/head:mr-{remote}-{self.gitlab_mr}'
git checkout --force 'mr-{remote}-{self.gitlab_mr}'
"""
        run_script(node=node, script=script, cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

    def _tofu_plan(self, node: Any, plan_file: str, op_dir: Path) -> None:
        chdir = f"-chdir={op_dir}"
        commands = [
            f"tofu {chdir} init",
            f"tofu {chdir} validate",
            f"tofu {chdir} plan -out={plan_file}",
        ]

        for command in commands:
            run_one_raw(
                command=command.split(),
                node=node,
                cumin_params=CUMIN_SAFE_WITHOUT_PROGRESS,
            )

    def _tofu_apply(self, node: Any, plan_file: str) -> None:
        # NOTE: not accepting a different op_dir because in theory we only allow to run
        # tofu apply for the main branch
        run_one_raw(
            command=["tofu", f"-chdir={self.TOFU_INFRA_ORIG_DIR}", "apply", plan_file],
            node=node,
            cumin_params=CUMIN_UNSAFE_WITHOUT_PROGRESS,
        )

    def _tofu_show_plan(self, node: Any, plan_file: str, op_dir: Path) -> str:
        try:
            plan = run_one_raw(
                command=["tofu", f"-chdir={op_dir}", "show", "-no-color", plan_file],
                node=node,
                cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
            )
        except Exception as e:  # pylint: disable=broad-except
            LOGGER.warning("WARNING: unable to run tofu show plan: %s", str(e))
            return ""

        return plan

    def _tofu_plan_is_noop(self, plan: str) -> bool:
        for line in plan.splitlines():
            if line == self.TOFU_NO_CHANGES_LINE:
                return True

        return False

    def _tofu_plan_to_gitlab_note(self, cluster_name: str, plan: str) -> None:
        if not self.gitlab_controller or not self.gitlab_mr:
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
        # otherwise, we would collapse every tofu plan
        plan_block_header = "```"
        plan_block_footer = "```"
        if len(plan.splitlines()) >= self.GITLAB_MR_NOTE_COLLAPSE_AT_LINES_THRESHOLD:
            plan_block_header = "<details><summary>Click to expand tofu plan</summary>"
            plan_block_footer = "</details>"

        note_body = f"""tofu plan was run for this merge request in cluster `{cluster_name}`:
{plan_block_header}
{plan}
{plan_block_footer}
        """
        self.gitlab_controller.create_mr_note(
            project_id=project_id, merge_request_iid=self.gitlab_mr, note_body=note_body
        )

    @contextmanager
    def _with_maybe_merge_request(self, node: Any) -> Generator[Path, None, None]:
        """Checkout the gitlab merge request in a temporal operation directory."""
        if not self.gitlab_mr:
            # noop
            yield self.TOFU_INFRA_ORIG_DIR
            return

        with with_temporary_dir(dst_node=node, prefix=f"tofu_infra_mr{self.gitlab_mr}_") as op_dir:
            LOGGER.info("INFO: checking out gitlab MR branch in %s", op_dir)
            self._git_checkout_mr(node, op_dir)
            yield op_dir

    def _run(self, cluster_name: str) -> None:
        """Run the routine"""

        control_node_fqdn = get_control_nodes(cluster_name=OpenstackClusterName(cluster_name))[0]

        LOGGER.info("INFO: running tofu for deployment '%s', node '%s'", cluster_name, control_node_fqdn)

        query = f"P{{{control_node_fqdn}}}"
        node = self.spicerack.remote().query(query, use_sudo=True)

        self._git_cleanup_and_checkout_main_branch(node)

        with with_temporary_file(dst_node=node, contents="", cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT) as plan_file:
            with self._with_maybe_merge_request(node) as op_dir:
                self._tofu_plan(node, plan_file=plan_file, op_dir=op_dir)
                plan = self._tofu_show_plan(node, plan_file=plan_file, op_dir=op_dir)
                self._tofu_plan_to_gitlab_note(cluster_name=cluster_name, plan=plan)

            plan_is_noop = self._tofu_plan_is_noop(plan=plan)

            if self.apply and not plan_is_noop:
                ask_confirmation(f"Before apply, Is tofu plan correct ({cluster_name} @ {control_node_fqdn})?")

                self._tofu_apply(node, plan_file=plan_file)

    def run_with_proxy(self) -> None:
        """Main entry point"""

        clusters = get_openstack_clusters()
        for cluster_name in clusters:
            if self.cluster_name and cluster_name != self.cluster_name:
                # user wanted to filter operations for a given deployment
                continue

            self._run(cluster_name=str(cluster_name))
