r"""WMCS Toolforge Kubernetes - build a docker image for a custom component

Usage example:
    cookbook wmcs.toolforge.k8s.component/build \
        --git-url https://gerrit.wikimedia.org/r/cloud/toolforge/jobs-framework-api
"""
from __future__ import annotations

import argparse
import logging
import random
import string

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import (
    CommonOpts,
    CuminParams,
    SALLogger,
    WMCSCookbookRunnerBase,
    add_common_opts,
    run_one_raw,
    with_common_opts,
)

LOGGER = logging.getLogger(__name__)


class ToolforgeComponentBuild(CookbookBase):
    """Build a docker image from a git repository for a Toolforge kubernetes custom component."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser, project_default="tools")
        parser.add_argument(
            "--registry-url",
            required=False,
            default="docker-registry.tools.wmflabs.org",
            help="docker registry URL",
        )
        parser.add_argument(
            "--docker-builder-hostname",
            required=False,
            default="tools-docker-imagebuilder-01",
            help="docker image builder virtual machine hostname",
        )
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
            "--docker-image-tag",
            required=False,
            help="docker tag for the new image, if not provided the git hash of the latest commit will be used",
        )
        parser.add_argument(
            "--docker-image-name",
            required=False,
            help="docker image name. If not provided, it will be guessed based on the git name",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, ToolforgeComponentBuildRunner,)(
            registry_url=args.registry_url,
            docker_builder_hostname=args.docker_builder_hostname,
            git_name=args.git_name,
            git_url=args.git_url,
            git_branch=args.git_branch,
            docker_image_tag=args.docker_image_tag,
            docker_image_name=args.docker_image_name,
            spicerack=self.spicerack,
        )


def _randomword(length):
    letters = string.ascii_lowercase
    return "".join(random.choice(letters) for i in range(length))  # nosec


def _sh_wrap(cmd: str) -> list[str]:
    return ["/bin/sh", "-c", "--", f"'{cmd}'"]


class ToolforgeComponentBuildRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeComponentBuild."""

    def __init__(
        self,
        common_opts: CommonOpts,
        registry_url: str,
        docker_builder_hostname: str,
        git_name: str,
        git_url: str,
        git_branch: str,
        docker_image_tag: str | None,
        docker_image_name: str,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        self.common_opts = common_opts
        self.registry_url = registry_url
        self.docker_builder_hostname = docker_builder_hostname
        self.git_name = git_name
        self.git_url = git_url
        self.git_branch = git_branch
        self.docker_image_tag = docker_image_tag
        self.docker_image_name = docker_image_name
        super().__init__(spicerack=spicerack)
        self.random_dir = f"/tmp/cookbook-toolforge-k8s-component-build-{_randomword(10)}"  # nosec
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )

        if not self.git_name:
            self.git_name = self.git_url.split("/")[-1]

            # remove trailing ".git" in case it was in the URL
            if self.git_name.endswith(".git"):
                self.git_name = self.git_name[:-4]

            LOGGER.info("INFO: guessed git tree name as %s", self.git_name)

        if not self.docker_image_name:
            # some special cases for docker image names :(
            if self.git_name == "maintain-kubeusers":
                self.docker_image_name = self.git_name
            elif self.git_name == "ingress-admission-controller":
                self.docker_image_name = "ingress-admission"
            elif self.git_name == "registry-admission-webhook":
                self.docker_image_name = "registry-admission"
            else:
                self.docker_image_name = f"toolforge-{self.git_name}"
            LOGGER.info("INFO: guessed docker image name as %s", self.docker_image_name)

    def run(self) -> None:
        """Main entry point"""
        remote = self.spicerack.remote()
        build_node_fqdn = f"{self.docker_builder_hostname}.{self.common_opts.project}.eqiad1.wikimedia.cloud"
        build_node = remote.query(f"D{{{build_node_fqdn}}}", use_sudo=True)
        LOGGER.info("INFO: using build node %s", build_node_fqdn)
        no_output = CuminParams(print_output=False, print_progress_bars=False)

        # create temp dir
        LOGGER.info("INFO: creating temp dir %s", self.random_dir)
        run_one_raw(node=build_node, command=["mkdir", self.random_dir], cumin_params=no_output)

        # git clone
        cmd = f"cd {self.random_dir} ; git clone {self.git_url}"
        LOGGER.info("INFO: git cloning %s", self.git_url)
        run_one_raw(node=build_node, command=_sh_wrap(cmd), cumin_params=no_output)

        # git checkout branch
        repo_dir = f"{self.random_dir}/{self.git_name}"
        cmd = f"cd {repo_dir} ; git checkout {self.git_branch}"
        LOGGER.info("INFO: git checkout %s on cloning %s", self.git_branch, repo_dir)
        run_one_raw(node=build_node, command=_sh_wrap(cmd), cumin_params=no_output)

        # get git hash for the SAL logger
        cmd = f"cd {repo_dir} ; git rev-parse --short HEAD"
        git_hash = run_one_raw(node=build_node, command=_sh_wrap(cmd), last_line_only=True, cumin_params=no_output)

        if not self.docker_image_tag:
            self.docker_image_tag = git_hash

        # docker build
        image = f"{self.docker_image_name}:{self.docker_image_tag}"
        cmd = f"cd {repo_dir} ; docker build -q --tag {image} ."
        LOGGER.info("INFO: building docker image %s", image)
        image_id = run_one_raw(node=build_node, command=_sh_wrap(cmd), last_line_only=True, cumin_params=no_output)

        # cleanup
        cmd = f"rm -rf --preserve-root=all {self.random_dir}"
        LOGGER.info("INFO: cleaning up temp dir %s", self.random_dir)
        run_one_raw(node=build_node, command=cmd.split(), cumin_params=no_output)

        # docker tag
        url = f"{self.registry_url}/{image}"
        cmd = f"docker tag {image_id} {url}"
        LOGGER.info("INFO: creating docker tag %s", url)
        run_one_raw(node=build_node, command=_sh_wrap(cmd), cumin_params=no_output)

        # docker push
        cmd = f"docker push {url}"
        LOGGER.info("INFO: pushing to the registry %s", url)
        run_one_raw(node=build_node, command=_sh_wrap(cmd), cumin_params=no_output)

        self.sallogger.log(message=f"build & push docker image {url} from {self.git_url} ({git_hash})")
