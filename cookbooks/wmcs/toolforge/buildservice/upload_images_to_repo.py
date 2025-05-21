r"""WMCS Toolforge - Upload the needed images for the buildservice to the toolforge repo

Usage example:
    cookbook wmcs.toolforge.buildservice.upload_images_to_repo \
        --tekton-version v0.33.2 \
        --bash-version 5.1.4 \
        --lifecycle-version 0.10.2

"""

# pylint: disable=too-many-arguments
from __future__ import annotations

import argparse

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.k8s.images import ImageController


class UploadImagesToRepo(CookbookBase):
    """Uploads the external buildservice images to the local toolforge repository for local comsumption."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser, project_default="toolsbeta")
        parser.add_argument(
            "--tekton-version",
            required=False,
            default=None,
            help="Tag for the tekton images to pull",
        )
        parser.add_argument(
            "--bash-version",
            required=False,
            default=None,
            help="Tag for the bash image to use.",
        )
        parser.add_argument(
            "--lifecycle-version",
            required=False,
            default=None,
            help="Tag for the buildpacks lifecycle image to use.",
        )
        parser.add_argument(
            "--upload-distroless",
            required=False,
            action="store_true",
            help="Upload the distroless image.",
        )
        parser.add_argument(
            "--image-repo-url",
            required=False,
            default="docker-registry.svc.toolforge.org",
            help="Repository to upload the images to.",
        )
        parser.add_argument(
            "--uploader-node",
            required=False,
            default="tools-docker-imagebuilder-01.tools.eqiad1.wikimedia.cloud",
            help="Host to use to pull and push to the given repository.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, UploadImagesToRepoRunner)(
            tekton_version=args.tekton_version,
            lifecycle_version=args.lifecycle_version,
            bash_version=args.bash_version,
            image_repo_url=args.image_repo_url,
            uploader_node=args.uploader_node,
            upload_distroless=args.upload_distroless,
            spicerack=self.spicerack,
        )


class UploadImagesToRepoRunner(WMCSCookbookRunnerBase):
    """Runner for UploadImagesToRepo."""

    TEKTON_COMMON_PATH = "gcr.io/tekton-releases/github.com/tektoncd/pipeline/cmd"
    TEKTON_IMAGES = [
        "controller",
        "entrypoint",
        "git-init",
        "imagedigestexporter",
        "kubeconfigwriter",
        "nop",
        "pullrequest-init",
        "webhook",
        "workingdirinit",
    ]

    def __init__(
        self,
        common_opts: CommonOpts,
        image_repo_url: str,
        uploader_node: str,
        tekton_version: str | None,
        lifecycle_version: str | None,
        bash_version: str | None,
        upload_distroless,
        spicerack: Spicerack,
    ):
        """Init"""
        self.tekton_version = tekton_version
        self.lifecycle_version = lifecycle_version
        self.bash_version = bash_version
        self.image_repo_url = image_repo_url
        self.uploader_node = uploader_node
        self.upload_distroless = upload_distroless
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run(self) -> None:
        """Main entry point"""
        remote = self.spicerack.remote()
        uploader_node = remote.query(f"D{{{self.uploader_node}}}", use_sudo=True)
        image_ctrl = ImageController(spicerack=self.spicerack, uploader_node=uploader_node)

        if self.tekton_version:
            for image_name in self.TEKTON_IMAGES:
                pull_url = f"{self.TEKTON_COMMON_PATH}/{image_name}:{self.tekton_version}"
                push_url = f"{self.image_repo_url}/toolforge-tektoncd-pipeline-cmd-{image_name}:{self.tekton_version}"
                image_ctrl.update_image(pull_url=pull_url, push_url=push_url)

        if self.bash_version:
            pull_url = f"docker.io/library/bash:{self.bash_version}"
            push_url = f"{self.image_repo_url}/toolforge-library-bash:{self.bash_version}"
            image_ctrl.update_image(pull_url=pull_url, push_url=push_url)

        if self.lifecycle_version:
            pull_url = f"docker.io/buildpacksio/lifecycle:{self.lifecycle_version}"
            push_url = f"{self.image_repo_url}/toolforge-buildpacksio-lifecycle:{self.lifecycle_version}"
            image_ctrl.update_image(pull_url=pull_url, push_url=push_url)

        if self.upload_distroless:
            # the distroless/base:debug image contains sh entrypoint which is required
            # for the tekton pipeline to work. Only the :debug one contains it
            pull_url = "gcr.io/distroless/base:debug"
            push_url = f"{self.image_repo_url}/toolforge-distroless-base-debug:latest"
            image_ctrl.update_image(
                pull_url=pull_url,
                push_url=push_url,
            )
