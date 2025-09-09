r"""WMCS Toolforge - Upload the needed container images to the toolforge container image registry

Usage example:
    cookbook wmcs.toolforge.k8s.image.copy_to_registry
"""

from __future__ import annotations

import argparse

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.k8s.images import ImageController


class GenericCopyImagesToRepo(CookbookBase):
    """Uploads the external images to the local toolforge repository for local comsumption."""

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser, project_default="tools")
        parser.add_argument(
            "--image-repo-url",
            required=False,
            default="docker-registry.svc.toolforge.org",
            help="Repository to upload the images to.",
        )
        parser.add_argument(
            "--uploader-node",
            required=False,
            default="tools-imagebuilder-2.tools.eqiad1.wikimedia.cloud",
            help="Host to use to pull and push to the given repository.",
        )
        parser.add_argument(
            "--origin-image",
            required=True,
            help="Original image to pull. Example: 'quay.io/something:v1.2.3'",
        )
        parser.add_argument(
            "--dest-image-name",
            required=True,
            help="Destination image name. Example: 'something'",
        )
        parser.add_argument(
            "--dest-image-version",
            required=True,
            help="Destination image version. Example: 'v1.2.3'",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(self.spicerack, args, GenericCopyImagesToRepoRunner)(
            image_repo_url=args.image_repo_url,
            uploader_node=args.uploader_node,
            origin_image=args.origin_image,
            dest_image_name=args.dest_image_name,
            dest_image_version=args.dest_image_version,
            spicerack=self.spicerack,
        )


class GenericCopyImagesToRepoRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        image_repo_url: str,
        uploader_node: str,
        origin_image: str,
        dest_image_name: str,
        dest_image_version: str,
        spicerack: Spicerack,
    ):

        self.uploader_node = uploader_node
        self.pull_url = origin_image
        self.push_url = f"{image_repo_url}/{dest_image_name}:{dest_image_version}"
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run(self) -> None:

        remote = self.spicerack.remote()
        uploader_node = remote.query(f"D{{{self.uploader_node}}}", use_sudo=True)
        image_ctrl = ImageController(spicerack=self.spicerack, uploader_node=uploader_node)

        image_ctrl.update_image(pull_url=self.pull_url, push_url=self.push_url)
