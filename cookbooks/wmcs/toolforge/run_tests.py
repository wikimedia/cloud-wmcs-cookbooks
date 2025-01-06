r"""WMCS Toolforge - run functional tests

Usage example:
    cookbook wmcs.toolforge.tests \
        --cluster-name toolsbeta \
        --filter-tags direct-api \
        --filter-tags builds-api
"""

from __future__ import annotations

import argparse
import logging

from spicerack import RemoteHosts, Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from spicerack.remote import RemoteExecutionError

from wmcs_libs.common import (
    CUMIN_UNSAFE_WITH_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    CommonOpts,
    WMCSCookbookRunnerBase,
    run_one_raw,
    run_script,
)
from wmcs_libs.inventory.static import get_static_inventory
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import add_toolforge_kubernetes_cluster_opts, with_toolforge_kubernetes_cluster_opts

LOGGER = logging.getLogger(__name__)

DEPLOY_REPO_URL = "https://gitlab.wikimedia.org/repos/cloud/toolforge/toolforge-deploy.git"
# Root has no access to k8s from the bastions
TESTS_USER = "dcaro"


class ToolforgeRunTests(CookbookBase):
    """Deploy a kubernetes custom component in Toolforge."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_toolforge_kubernetes_cluster_opts(parser)
        parser.add_argument("--branch", default="main", help="branch to run tests on")
        parser.add_argument("--filter-tags", action="append", default=[], help="filter tests with the given tags")
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeRunTestsRunner)(
            spicerack=self.spicerack,
            filter_tags=args.filter_tags,
            branch=args.branch,
        )


class ToolforgeRunTestsRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeRunTests."""

    git_hash: str | None = None

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        filter_tags: list[str],
        spicerack: Spicerack,
        branch: str = "main",
    ):
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.filter_tags = filter_tags
        self.branch = branch
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run_with_proxy(self) -> None:
        test_result = self.run_tests(filter_tags=self.filter_tags, branch=self.branch)
        if "FAILED" in test_result["status"]:
            raise Exception(f"FAILED:\n{test_result['logs']}")

    def run_tests(self, filter_tags: list[str], branch: str) -> dict[str, str]:
        site = self.cluster_name.get_openstack_cluster_name().get_site()
        bastions_fqdns = (
            get_static_inventory()[site]
            .clusters_by_type[self.cluster_name.get_type()][self.cluster_name]
            .nodes_by_role[ToolforgeKubernetesNodeRoleName.BASTION]
        )
        chosen_bastion = bastions_fqdns[0]
        bastion_node = self.spicerack.remote().query(f"D{{{chosen_bastion}}}", use_sudo=True)
        test_logs = run_one_raw(
            # TERM needed for bats(tput actually) to run properly
            command=[
                "env",
                "TERM=xterm-256color",
                "toolforge-deploy/utils/run_functional_tests.sh",
                "-r",
                "-b",
                branch,
                "--",
            ]
            + [f"--filter-tags {tag}" for tag in filter_tags],
            user=TESTS_USER,
            node=bastion_node,
            capture_errors=True,
            cumin_params=CUMIN_UNSAFE_WITH_OUTPUT,
        )

        status = "🗹 PASSED"
        if test_logs.count(" 0 failures ") != 2:  # both admin and tools tests must all pass
            status = "🗷 FAILED"

        if filter_tags:
            status += f" (ran tests {filter_tags})"
        else:
            status += " (ran all tests)"

        return {
            "status": status,
            "logs": test_logs,
        }

    def _ensure_deploy_repo_cloned(self, bastion_node: RemoteHosts) -> None:
        try:
            run_one_raw(
                command=["test", "-e", "toolforge-deploy/.git"],
                user=TESTS_USER,
                node=bastion_node,
                cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
            )
        except RemoteExecutionError as error:
            if "exit_code=2" in str(error):
                self._clone_deploy_repo(bastion_node)
            else:
                raise

        self._update_deploy_repo(bastion_node)

    def _clone_deploy_repo(self, bastion_node: RemoteHosts) -> None:
        run_one_raw(
            command=["git", "clone", DEPLOY_REPO_URL],
            user=TESTS_USER,
            node=bastion_node,
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )

    def _update_deploy_repo(self, bastion_node: RemoteHosts) -> None:
        run_script(
            script="""set -o errexit
set -o nounset
set -o pipefail

cd "toolforge-deploy"
git fetch --all
git reset --hard FETCH_HEAD
git clean -fdx
""",
            user=TESTS_USER,
            node=bastion_node,
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )
