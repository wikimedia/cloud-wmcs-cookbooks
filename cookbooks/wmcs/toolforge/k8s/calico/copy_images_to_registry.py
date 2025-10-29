"""Cookbook to update Calico container images."""

from __future__ import annotations

import argparse

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.k8s.images import ImageController
from wmcs_libs.k8s.kubernetes import validate_v_version

IMAGES = (
    "cni",
    "ctl",
    "kube-controllers",
    "node",
    "typha",
)


class CopyImagesToRepo(CookbookBase):
    r"""WMCS Toolforge - Upload the needed container images for Calico to the Toolforge container image registry

    Usage example:
        cookbook wmcs.toolforge.k8s.calico.copy_images_to_registry --calico-version v3.xx.y
    """

    def argument_parser(self):
        parser = super().argument_parser()
        add_common_opts(parser, project_default="tools")
        parser.add_argument(
            "--image-repo-url",
            required=False,
            default="docker-registry.svc.toolforge.org",
            help="Repository to upload the images to",
        )
        parser.add_argument(
            "--uploader-node",
            required=False,
            default="tools-imagebuilder-2.tools.eqiad1.wikimedia.cloud",
            help="Host to use to pull and push to the given repository",
        )
        parser.add_argument(
            "--calico-version",
            required=True,
            type=validate_v_version,
            help="Version of Calico to upgrade to (matches the image tag).",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_common_opts(self.spicerack, args, CopyImagesToRepoRunner)(
            image_repo_url=args.image_repo_url,
            uploader_node=args.uploader_node,
            calico_version=args.calico_version,
            spicerack=self.spicerack,
        )


class CopyImagesToRepoRunner(WMCSCookbookRunnerBase):
    def __init__(
        self,
        common_opts: CommonOpts,
        image_repo_url: str,
        uploader_node: str,
        calico_version: str,
        spicerack: Spicerack,
    ):
        self.image_repo_url = image_repo_url
        self.uploader_node = uploader_node
        self.calico_version = calico_version
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        return f"for Calico {self.calico_version}"

    def run(self) -> None:
        remote = self.spicerack.remote()
        uploader_node = remote.query(f"D{{{self.uploader_node}}}", use_sudo=True)
        image_ctrl = ImageController(spicerack=self.spicerack, uploader_node=uploader_node)

        for image in IMAGES:
            image_ctrl.update_image(
                pull_url=f"docker.io/calico/{image}:{self.calico_version}",
                push_url=f"{self.image_repo_url}/calico/{image}:{self.calico_version}",
            )
