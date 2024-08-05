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
import string
from pathlib import Path

from spicerack import RemoteHosts, Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.aptly import SUPPORTED_DISTROS, Aptly
from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    CommonOpts,
    WMCSCookbookRunnerBase,
    run_one_raw,
)
from wmcs_libs.gitlab import get_artifacts_url
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
    "jobs-cli": "toolforge-jobs-framework-cli",
    "tools-webservice": "toolforge-webservice",
    "envvars-cli": "toolforge-envvars-cli",
    "builds-cli": "toolforge-builds-cli",
    "toolforge-cli": "toolforge-cli",
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
            default="main",
            help="git branch in the source repository",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeComponentDeployRunner)(
            component=args.component,
            git_branch=args.git_branch,
            spicerack=self.spicerack,
        )


def _random_word(length):
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(length))  # nosec


class ToolforgeComponentDeployRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeComponentDeploy."""

    git_hash: str | None = None

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        component: str,
        git_branch: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.component = component
        self.git_branch = git_branch
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.random_dir = f"/tmp/cookbook-toolforge-k8s-component-deploy-{_random_word(10)}"  # nosec

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

    def _upload_package_to_repos(
        self,
        cluster_name: ToolforgeKubernetesClusterName,
        component: str,
        branch: str | None = None,
    ) -> None:
        site = cluster_name.get_openstack_cluster_name().get_site()
        service_node_fqdn = (
            get_static_inventory()[site]
            .clusters_by_type[cluster_name.get_type()][cluster_name]
            .nodes_by_role[ToolforgeKubernetesNodeRoleName.SERVICES]
        )[0]
        service_node = self.spicerack.remote().query(f"D{{{service_node_fqdn}}}", use_sudo=True)

        artifacts_url = get_artifacts_url(component=component, branch=branch or "main")

        project = cluster_name.value
        LOGGER.info(
            "INFO: Uploading artifacts to %s at %s (same node for tools and toolsbeta)",
            service_node_fqdn,
            self.random_dir,
        )
        package = COMPONENT_TO_PACKAGE_NAME[component]
        command = f"""bash -c -- '
set -o errexit;
set -o nounset;
set -o pipefail;

mkdir -p "{self.random_dir}";
cd "{self.random_dir}";
wget "{artifacts_url}";
unzip artifacts;
'
"""
        run_one_raw(command=[command], node=service_node, cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

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

    def _install_package_on_bastions(self, component: str, cluster_name: ToolforgeKubernetesClusterName) -> None:
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
            command=["apt", "install", "--upgrade", package], node=bastions, cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT
        )
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
    ) -> None:
        self._upload_package_to_repos(cluster_name=cluster_name, branch=branch, component=component)
        self._install_package_on_bastions(component=component, cluster_name=cluster_name)

    def _deploy_k8s_component(
        self, component: str, git_branch: str, cluster_name: ToolforgeKubernetesClusterName
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
        cmd = f"{repo_dir}/deploy.sh '{component}'"
        LOGGER.info("INFO: deploying ...")
        run_one_raw(node=deploy_node, command=[cmd], cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

        self._cleanup_temp_dir(node=deploy_node)
        LOGGER.info("INFO: deployed %s on %s from branch %s", component, cluster_name.value, git_branch)
