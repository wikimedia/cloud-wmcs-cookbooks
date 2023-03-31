r"""WMCS Toolforge Kubernetes - deploy a kubernetes custom component

Usage example:
    cookbook wmcs.toolforge.k8s.component.deploy \
        --git-url https://gerrit.wikimedia.org/r/cloud/toolforge/jobs-framework-api
"""
from __future__ import annotations

import argparse
import logging
import random
import string

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, CuminParams, SALLogger, WMCSCookbookRunnerBase, run_one_raw
from wmcs_libs.inventory import ToolforgeKubernetesClusterName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)

LOGGER = logging.getLogger(__name__)


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
        parser.add_argument(
            "--git-url",
            required=True,
            help="git URL for the source code",
        )
        parser.add_argument(
            "--git-name",
            required=False,
            help="git repository name. If not provided, it will be guessed based on the git URL",
        )
        parser.add_argument(
            "--git-branch",
            required=False,
            default="main",
            help="git branch in the source repository",
        )
        parser.add_argument(
            "--deployment-command",
            required=False,
            default="./deploy.sh",
            help="command to trigger the deployment.",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeComponentDeployRunner)(
            git_url=args.git_url,
            git_name=args.git_name,
            git_branch=args.git_branch,
            deployment_command=args.deployment_command,
            spicerack=self.spicerack,
        )


def _randomword(length):
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(length))  # nosec


def _sh_wrap(cmd: str) -> list[str]:
    return ["/bin/sh", "-c", "--", f"'{cmd}'"]


class ToolforgeComponentDeployRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeComponentDeploy."""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        git_url: str,
        git_name: str,
        git_branch: str,
        deployment_command: str,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.git_url = git_url
        self.git_name = git_name
        self.git_branch = git_branch
        self.deployment_command = deployment_command
        super().__init__(spicerack=spicerack)
        self.random_dir = f"/tmp/cookbook-toolforge-k8s-component-deploy-{_randomword(10)}"  # nosec
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )

        if not self.git_name:
            self.git_name = self.git_url.split("/")[-1]

            # remove trailing ".git" in case it was in the URL
            if self.git_name.endswith(".git"):
                self.git_name = self.git_name[:-4]

            LOGGER.info("INFO: guessed git tree name as %s", self.git_name)

    def run(self) -> None:
        """Main entry point"""
        remote = self.spicerack.remote()
        deploy_node_fqdn = get_control_nodes(self.cluster_name)[0]
        deploy_node = remote.query(f"D{{{deploy_node_fqdn}}}", use_sudo=True)
        LOGGER.info("INFO: using deploy node %s", deploy_node_fqdn)
        no_output = CuminParams(print_output=False, print_progress_bars=False)

        # create temp dir
        LOGGER.info("INFO: creating temp dir %s", self.random_dir)
        run_one_raw(node=deploy_node, command=["mkdir", self.random_dir], cumin_params=no_output)

        # git clone
        cmd = f"cd {self.random_dir} ; git clone {self.git_url}"
        LOGGER.info("INFO: git cloning %s", self.git_url)
        run_one_raw(node=deploy_node, command=_sh_wrap(cmd), cumin_params=no_output)

        # git checkout branch
        repo_dir = f"{self.random_dir}/{self.git_name}"
        cmd = f"cd {repo_dir} ; git checkout {self.git_branch}"
        LOGGER.info("INFO: git checkout branch '%s' on %s", self.git_branch, repo_dir)
        run_one_raw(node=deploy_node, command=_sh_wrap(cmd), cumin_params=no_output)

        # get git hash for the SAL logger
        cmd = f"cd {repo_dir} ; git rev-parse --short HEAD"
        git_hash = run_one_raw(node=deploy_node, command=_sh_wrap(cmd), last_line_only=True, cumin_params=no_output)

        # deploy!
        cmd = f"cd {repo_dir} ; {self.deployment_command}"
        LOGGER.info("INFO: deploying with %s", self.deployment_command)
        run_one_raw(node=deploy_node, command=_sh_wrap(cmd), cumin_params=CuminParams(print_progress_bars=False))

        # cleanup
        cmd = f"rm -rf --preserve-root=all {self.random_dir}"
        LOGGER.info("INFO: cleaning up temp dir %s", self.random_dir)
        run_one_raw(node=deploy_node, command=cmd.split(), cumin_params=no_output)

        self.sallogger.log(message=f"deployed kubernetes component {self.git_url} ({git_hash})")
