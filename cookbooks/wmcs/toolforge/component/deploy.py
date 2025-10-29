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
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.toolforge.run_tests import ToolforgeRunTestsRunner
from wmcs_libs.aptly import SUPPORTED_DISTROS, Aptly
from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CUMIN_UNSAFE_WITH_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    CommonOpts,
    WMCSCookbookRunnerBase,
    run_one_raw,
    run_script,
)
from wmcs_libs.inventory.static import get_static_inventory
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.wm_gitlab import GitlabController, MrNotFound, get_branch_mr, get_project

LOGGER = logging.getLogger(__name__)

DEPLOY_REPO_URL = "https://gitlab.wikimedia.org/repos/cloud/toolforge/toolforge-deploy.git"
COMPONENT_TO_PACKAGE_NAME = {
    "builds-cli": "toolforge-builds-cli",
    "components-cli": "toolforge-components-cli",
    "envvars-cli": "toolforge-envvars-cli",
    "jobs-cli": "toolforge-jobs-cli",
    "misctools-cli": "toolforge-misctools-cli",
    "toolforge-cli": "toolforge-cli",
    "toolforge-weld": "python3-toolforge-weld",
    "webservice-cli": "toolforge-webservice",
}


class ToolforgeComponentDeploy(CookbookBase):
    """Deploy a kubernetes custom component in Toolforge."""

    def argument_parser(self):

        parser = super().argument_parser()
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
            "--run-all-tests",
            action="store_true",
            help="If passed, will run all the tests, not only the ones related to the given component.",
        )
        parser.add_argument(
            "--no-wait",
            action="store_true",
            help="(k8s components only) If passed, it will not wait for the helm deployment to finish up.",
        )
        parser.add_argument("--filter-tags", action="append", default=[], help="filter tests with the given tags")
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeComponentDeployRunner)(
            component=args.component,
            git_branch=args.git_branch,
            run_tests=not args.skip_tests,
            wait=not args.no_wait,
            filter_tags=args.filter_tags,
            run_all_tests=args.run_all_tests,
            spicerack=self.spicerack,
        )


def _random_word(length):
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(length))  # nosec


class ToolforgeComponentDeployRunner(WMCSCookbookRunnerBase):

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
        run_all_tests: bool,
        spicerack: Spicerack,
    ):

        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.component = component
        self.git_branch = git_branch or f"bump_{component}"
        self.run_tests = run_tests
        self.run_all_tests = run_all_tests
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

        if self.component in COMPONENT_TO_PACKAGE_NAME:
            self._deploy_package(component=self.component, cluster_name=self.cluster_name, branch=self.git_branch)
        else:
            self._deploy_k8s_component(
                component=self.component,
                git_branch=self.git_branch,
                cluster_name=self.cluster_name,
            )

        if self.run_tests:
            tests_results = self._run_tests(
                cluster_name=self.cluster_name,
                branch=self.git_branch,
                filter_tags=self.filter_tags,
                component=self.component,
                run_all_tests=self.run_all_tests,
            )

            if "PASSED" not in tests_results["status"]:
                raise Exception(f"Failed deploying {self.component} in {self.cluster_name} (see logs for details)")

    def _run_tests(
        self,
        cluster_name: ToolforgeKubernetesClusterName,
        branch: str,
        filter_tags: list[str],
        component: str,
        run_all_tests: bool,
    ) -> dict[str, str]:
        tests_cookbook = ToolforgeRunTestsRunner(
            common_opts=self.common_opts,
            cluster_name=cluster_name,
            spicerack=self.spicerack,
            # these ones is not really used as we use the internal method
            branch=branch,
            filter_tags=filter_tags,
        )
        test_results = tests_cookbook.run_tests(
            filter_tags=filter_tags, branch=branch, component=None if run_all_tests else component
        )

        try:
            self._send_mr_comment(
                test_result=test_results, branch=branch, cluster_name=cluster_name, component=component
            )
        except MrNotFound:
            LOGGER.warning("Unable to find an MR for branch %s, skipping sending a comment.", branch)

        return test_results

    def _send_mr_comment(
        self,
        test_result: dict[str, str],
        branch: str,
        cluster_name: ToolforgeKubernetesClusterName,
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

        logs = self._cleanup_terminal_colors(test_result["logs"])
        # DO NOT CHANGE without updating https://gitlab.wikimedia.org/repos/cloud/toolforge/toolforge-deploy/-/blob/main/utils/run_functional_tests.sh  # noqa: E501
        version_output_delimiter = "-" * 47
        pre_version = logs.split(version_output_delimiter, 1)[0]
        version = logs.split(version_output_delimiter, 1)[1]
        version, post_version = version.split(version_output_delimiter, 1)

        note = self.gitlab_controller.create_mr_note(
            project_id=project["id"],
            merge_request_iid=mr_iid,
            note_body=f"""Ran the tests on {cluster_name}: **{test_result["status"]}**
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

    def _download_packages(self, component: str, artifact_urls: list[str], service_node: RemoteHosts) -> list[Path]:
        package_paths: list[Path] = []
        package = COMPONENT_TO_PACKAGE_NAME[component]
        for artifact_url in artifact_urls:
            script = f"""
    set -o errexit
    set -o nounset
    set -o pipefail

    mkdir -p "{self.random_dir}"
    cd "{self.random_dir}"
    wget "{artifact_url}" -O artifacts
    unzip artifacts
    """
            run_script(script=script, node=service_node, cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

        paths = run_one_raw(
            command=["ls", f"{self.random_dir}/debs/{package}*.deb"],
            node=service_node,
            cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
        ).splitlines()
        for path in paths:
            package_paths.append(Path(path))
        return package_paths

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

        artifact_urls = self.gitlab_controller.get_artifact_urls(component=component, branch=branch or "main")

        project = cluster_name.value
        LOGGER.info(
            "INFO: Uploading artifacts to %s at %s (same node for tools and toolsbeta)",
            service_node_fqdn,
            self.random_dir,
        )
        package_paths = self._download_packages(
            component=component, artifact_urls=artifact_urls, service_node=service_node
        )
        aptly = Aptly(command_runner_node=service_node)
        for distro in SUPPORTED_DISTROS:
            # TODO: remove once we don't have buster bastions
            if component in ["misctools-cli", "webservice-cli"] and distro == "buster":
                continue

            repo = f"{distro}-{project}"
            LOGGER.info("INFO: Publishing artifacts on repo %s", repo)
            for package_path in package_paths:
                aptly.add(package_path=package_path, repository=repo)
            aptly.publish(repository=repo)

        self._cleanup_temp_dir(node=service_node)

        # We rely on the format <package>_<version>_<arch>.deb
        package = COMPONENT_TO_PACKAGE_NAME[component]
        package_version = package_paths[0].name.split(f"{package}_", 1)[-1].rsplit("_", 1)[0]
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
        run_one_raw(node=deploy_node, command=[cmd], cumin_params=CUMIN_UNSAFE_WITH_OUTPUT)

        self._cleanup_temp_dir(node=deploy_node)
        LOGGER.info("INFO: deployed %s on %s from branch %s", component, cluster_name.value, git_branch)
