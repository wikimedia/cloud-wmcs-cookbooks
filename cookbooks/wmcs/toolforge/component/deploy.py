r"""WMCS Toolforge - deploy a custom component

Usage example:
    cookbook wmcs.toolforge.component.deploy \
        --cluster-name toolsbeta \
        --component jobs-api


    cookbook wmcs.toolforge.component.deploy \
        --cluster-name toolsbeta \
        --component builds-cli \
        --git-branch bump_to_0.0.18

"""

from __future__ import annotations

import argparse
import logging
import random
import re
import string
from pathlib import Path

from spicerack import RemoteHosts, Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from cookbooks.wmcs.toolforge.run_tests import ToolforgeRunTestsRunner
from wmcs_libs.aptly import SUPPORTED_DISTROS, Aptly
from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    CommonOpts,
    WMCSCookbookRunnerBase,
    run_one_raw,
    run_script,
)
from wmcs_libs.gitlab import GitlabController, MrNotFound, get_branch_mr, get_project
from wmcs_libs.inventory.static import get_static_inventory
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)

LOGGER = logging.getLogger(__name__)

DEPLOY_REPO_URL = "https://gitlab.wikimedia.org/repos/cloud/toolforge/toolforge-deploy.git"
COMPONENT_TO_PACKAGE_NAME = {
    "builds-cli": "toolforge-builds-cli",
    "components-cli": "toolforge-components-cli",
    "envvars-cli": "toolforge-envvars-cli",
    "jobs-cli": "toolforge-jobs-framework-cli",
    "toolforge-cli": "toolforge-cli",
    "toolforge-weld": "python3-toolforge-weld",
    "tools-webservice": "toolforge-webservice",
}


class ToolforgeComponentDeploy(CookbookBase):
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
        parser.add_argument("--component", required=True, help="component to deploy from the toolforge-deploy repo")
        parser.add_argument(
            "--git-branch",
            required=False,
            default=None,
            help=(
                "git branch in the source repository, will use 'bump_{component}' by default (force it to be 'main' "
                "if you want to deploy main)"
            ),
        )
        parser.add_argument(
            "--skip-tests",
            action="store_true",
            help="If passed, will skip running the tests.",
        )
        parser.add_argument(
            "--no-wait",
            action="store_true",
            help="(k8s components only) If passed, it will not wait for the helm deployment to finish up.",
        )
        parser.add_argument("--filter-tags", action="append", default=[], help="filter tests with the given tags")
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeComponentDeployRunner)(
            component=args.component,
            git_branch=args.git_branch,
            run_tests=not args.skip_tests,
            wait=not args.no_wait,
            filter_tags=args.filter_tags,
            spicerack=self.spicerack,
        )


def _random_word(length):
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(length))  # nosec


class ToolforgeComponentDeployRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeComponentDeploy."""

    git_hash: str | None = None

    def __init__(  # pylint: disable=too-many-arguments
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        component: str,
        git_branch: str | None,
        run_tests: bool,
        wait: bool,
        filter_tags: list[str],
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.component = component
        self.git_branch = git_branch or f"bump_{component}"
        self.run_tests = run_tests
        self.wait = wait
        self.filter_tags = filter_tags
        if filter_tags and not run_tests:
            raise Exception("You passed --filter-tags but also --skip-tests, only one of them is allowed.")

        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.random_dir = f"/tmp/cookbook-toolforge-k8s-component-deploy-{_random_word(10)}"  # nosec

        self.gitlab_controller = GitlabController(private_token=self.wmcs_config.get("gitlab_token", None))

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        git_hash = f" ({self.git_hash})" if self.git_hash else ""
        return f"for component {self.component}{git_hash}"

    def run_with_proxy(self) -> None:
        """Main entry point"""
        if self.component in COMPONENT_TO_PACKAGE_NAME:
            self._deploy_package(component=self.component, cluster_name=self.cluster_name, branch=self.git_branch)
        else:
            self._deploy_k8s_component(
                component=self.component,
                git_branch=self.git_branch,
                cluster_name=self.cluster_name,
            )

        if self.run_tests:
            self._run_tests(
                cluster_name=self.cluster_name,
                branch=self.git_branch,
                filter_tags=self.filter_tags,
                component=self.component,
            )

    def _run_tests(
        self,
        cluster_name: ToolforgeKubernetesClusterName,
        branch: str,
        filter_tags: list[str],
        component: str,
    ):
        tests_cookbook = ToolforgeRunTestsRunner(
            common_opts=self.common_opts,
            cluster_name=cluster_name,
            spicerack=self.spicerack,
            # this one is not really used as we use the internal method
            filter_tags=filter_tags,
        )
        test_logs = tests_cookbook.run_tests(filter_tags=filter_tags)

        try:
            self._send_mr_comment(
                logs=test_logs, branch=branch, cluster_name=cluster_name, filter_tags=filter_tags, component=component
            )
        except MrNotFound:
            LOGGER.warning("Unable to find an MR for branch %s, skipping sending a comment.", branch)

    def _send_mr_comment(
        self,
        logs: str,
        branch: str,
        cluster_name: ToolforgeKubernetesClusterName,
        filter_tags: list[str],
        component: str,
    ) -> None:
        if component in COMPONENT_TO_PACKAGE_NAME:
            # packages MRs bumping the version is on the repo of the package
            project_name = component
        else:
            # k8s components have them in toolforge-deploy
            project_name = "toolforge-deploy"

        project = get_project(component=project_name)
        mr_iid = get_branch_mr(branch=branch, project=project)

        status = "🗹 PASSED"
        if " 0 failures " not in logs:
            status = "🗷 FAILED"

        if filter_tags:
            status += f" (ran tests {filter_tags})"
        else:
            status += " (ran all tests)"

        logs = self._cleanup_terminal_colors(logs)
        pre_version = logs.split("toolforge components versions:", 1)[0] + "toolforge components versions:"
        version = logs.split("toolforge components versions:", 1)[1]
        version, post_version = version.split("Running tests from branch: ", 1)
        post_version = "Running tests from branch: " + post_version

        note = self.gitlab_controller.create_mr_note(
            project_id=project["id"],
            merge_request_iid=mr_iid,
            note_body=f"""Ran the tests on {cluster_name}: **{status}**
```
{pre_version}
```

{version}

```
{post_version}
```
""",
        )
        mr_link = (
            "https://gitlab.wikimedia.org/repos/cloud/toolforge/"
            f"{project_name}/-/merge_requests/{note.mr_iid}#note_{note.get_id()}"
        )
        LOGGER.info("Wrote note in MR: %s", mr_link)
        if " 0 failures " not in logs:
            raise Exception(f"TESTS FAILED:\n{logs}")

    def _cleanup_terminal_colors(self, logs: str) -> str:
        # TODO: find a nicer way, changing the TERM var did not help
        logs = re.sub(r"\[[0-9;]*m", "", logs)
        logs = re.sub(r"\[1G.*\[1G", "", logs)
        logs = re.sub(r"\[K", "", logs)

        return logs

    def _upload_package_to_repos(
        self,
        cluster_name: ToolforgeKubernetesClusterName,
        component: str,
        branch: str | None = None,
    ) -> str:
        site = cluster_name.get_openstack_cluster_name().get_site()
        service_node_fqdn = (
            get_static_inventory()[site]
            .clusters_by_type[cluster_name.get_type()][cluster_name]
            .nodes_by_role[ToolforgeKubernetesNodeRoleName.SERVICES]
        )[0]
        service_node = self.spicerack.remote().query(f"D{{{service_node_fqdn}}}", use_sudo=True)

        artifacts_url = self.gitlab_controller.get_artifacts_url(component=component, branch=branch or "main")

        project = cluster_name.value
        LOGGER.info(
            "INFO: Uploading artifacts to %s at %s (same node for tools and toolsbeta)",
            service_node_fqdn,
            self.random_dir,
        )
        package = COMPONENT_TO_PACKAGE_NAME[component]
        script = f"""
set -o errexit
set -o nounset
set -o pipefail

mkdir -p "{self.random_dir}"
cd "{self.random_dir}"
wget "{artifacts_url}"
unzip artifacts
"""
        run_script(script=script, node=service_node, cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

        package_path = Path(
            run_one_raw(
                command=["ls", f"{self.random_dir}/debs/{package}*.deb"],
                node=service_node,
                cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
            )
        )
        aptly = Aptly(command_runner_node=service_node)
        for distro in SUPPORTED_DISTROS:
            repo = f"{distro}-{project}"
            LOGGER.info("INFO: Publishing artifacts on repo %s", repo)
            aptly.add(package_path=package_path, repository=repo)
            aptly.publish(repository=repo)

        self._cleanup_temp_dir(node=service_node)

        # We rely on the format <package>_<version>_all.deb
        package_version = package_path.name.split(f"{package}_", 1)[-1].rsplit("_all.deb", 1)[0]
        return package_version

    def _install_package_on_bastions(
        self, component: str, cluster_name: ToolforgeKubernetesClusterName, version: str
    ) -> None:
        LOGGER.info("INFO: Installing packages on all bastions for project %s", cluster_name.value)
        site = cluster_name.get_openstack_cluster_name().get_site()
        bastions_fqdns = (
            get_static_inventory()[site]
            .clusters_by_type[cluster_name.get_type()][cluster_name]
            .nodes_by_role[ToolforgeKubernetesNodeRoleName.BASTION]
        )
        bastions = self.spicerack.remote().query(f"D{{{','.join(bastions_fqdns)}}}", use_sudo=True)

        package = COMPONENT_TO_PACKAGE_NAME[component]
        run_one_raw(command=["apt", "update"], node=bastions, cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT)
        run_one_raw(
            command=["apt", "install", "--yes", "--upgrade", f"{package}={version}"],
            node=bastions,
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )
        # to make sure that's the one that got installed
        package_version = next(
            line.split(":", 1)[-1]
            for line in run_one_raw(
                command=["apt", "policy", package],
                node=bastions,
                cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
            ).splitlines()
            if "Installed:" in line
        )
        LOGGER.info("INFO: installed %s version %s on %s", package, package_version, bastions)

    def _cleanup_temp_dir(self, node: RemoteHosts) -> None:
        cmd = f"rm -rf --preserve-root=all '{self.random_dir}'"
        LOGGER.info("INFO: cleaning up temp dir %s", self.random_dir)
        run_one_raw(node=node, command=cmd.split(), cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

    def _deploy_package(
        self, component: str, cluster_name: ToolforgeKubernetesClusterName, branch: str | None = None
    ) -> str:
        version = self._upload_package_to_repos(cluster_name=cluster_name, branch=branch, component=component)
        self._install_package_on_bastions(component=component, cluster_name=cluster_name, version=version)
        return version

    def _deploy_k8s_component(
        self, component: str, git_branch: str, cluster_name: ToolforgeKubernetesClusterName, wait: bool = True
    ) -> None:
        deploy_node_fqdn = get_control_nodes(self.cluster_name)[0]
        deploy_node = self.spicerack.remote().query(f"D{{{deploy_node_fqdn}}}", use_sudo=True)
        LOGGER.info("INFO: using deploy node %s", deploy_node_fqdn)

        LOGGER.info("INFO: creating temp dir %s", self.random_dir)
        run_one_raw(node=deploy_node, command=["mkdir", self.random_dir], cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

        cmd = f"git -C '{self.random_dir}' clone '{DEPLOY_REPO_URL}'"
        LOGGER.info("INFO: git cloning %s", DEPLOY_REPO_URL)
        run_one_raw(node=deploy_node, command=[cmd], cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

        git_name = DEPLOY_REPO_URL.rsplit("/", 1)[-1]
        if git_name.endswith(".git"):
            git_name = git_name[:-4]
        repo_dir = f"{self.random_dir}/{git_name}"
        cmd = f"git -C '{repo_dir}' checkout '{git_branch}'"
        LOGGER.info("INFO: git checkout branch '%s' on %s", git_branch, repo_dir)
        run_one_raw(node=deploy_node, command=[cmd], cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

        # get git hash for the SAL logger
        cmd = f"git -C '{repo_dir}' rev-parse --short HEAD"
        self.git_hash = run_one_raw(
            node=deploy_node, command=[cmd], last_line_only=True, cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT
        )

        # deploy!
        cmd = f"{repo_dir}/deploy.sh '{component}' {wait and '--wait' or ''}"
        LOGGER.info("INFO: deploying ...")
        run_one_raw(node=deploy_node, command=[cmd], cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

        self._cleanup_temp_dir(node=deploy_node)
        LOGGER.info("INFO: deployed %s on %s from branch %s", component, cluster_name.value, git_branch)
