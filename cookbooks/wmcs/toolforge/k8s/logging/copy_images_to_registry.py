r"""WMCS Toolforge - Upload the needed container images to the Toolforge container image registry

Usage example:
    cookbook wmcs.toolforge.k8s.logging.copy_images_to_registry
"""

from __future__ import annotations

import argparse

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.k8s.images import ImageController
from wmcs_libs.k8s.kubernetes import validate_version


class CopyImagesToRepo(CookbookBase):
    """Uploads the external Loki and Alloy images to the local Toolforge repository for local consumption."""

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
            "--loki-version",
            required=False,
            type=validate_version,
            help="Version of Loki to upgrade to (in N.N.N format).",
        )
        parser.add_argument(
            "--alloy-version",
            required=False,
            type=validate_version,
            help="Version of Alloy to upgrade to (in N.N.N format).",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(self.spicerack, args, CopyImagesToRepoRunner)(
            image_repo_url=args.image_repo_url,
            uploader_node=args.uploader_node,
            loki_version=args.loki_version,
            alloy_version=args.alloy_version,
            spicerack=self.spicerack,
        )


class CopyImagesToRepoRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        image_repo_url: str,
        uploader_node: str,
        loki_version: str | None,
        alloy_version: str | None,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments

        self.image_repo_url = image_repo_url
        self.uploader_node = uploader_node
        self.loki_version = loki_version
        self.alloy_version = alloy_version
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        components = []

        if self.loki_version:
            components.append(f"Loki {self.loki_version}")
        if self.alloy_version:
            components.append(f"Alloy {self.alloy_version}")

        return f"for {', '.join(components)}"

    def run(self) -> None:

        remote = self.spicerack.remote()
        uploader_node = remote.query(f"D{{{self.uploader_node}}}", use_sudo=True)

        image_ctrl = ImageController(spicerack=self.spicerack, uploader_node=uploader_node)

        if self.loki_version:
            image_ctrl.update_image(
                pull_url=f"docker.io/grafana/loki:{self.loki_version}",
                push_url=f"{self.image_repo_url}/grafana/loki:{self.loki_version}",
            )
        if self.alloy_version:
            image_ctrl.update_image(
                pull_url=f"docker.io/grafana/alloy:v{self.alloy_version}",
                push_url=f"{self.image_repo_url}/grafana/alloy:v{self.alloy_version}",
            )
