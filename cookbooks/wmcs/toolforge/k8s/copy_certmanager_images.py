"""WMCS Toolforge - Upload the cert-manager images to the Toolforge container image registry

Usage example:
    cookbook wmcs.toolforge.k8s.copy_certmanager_images --certmanager-version N.N.N
"""

import argparse

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, validate_version, with_common_opts
from wmcs_libs.k8s.images import ImageController


class CopyCertmanagerImagesToRepo(CookbookBase):
    """Uploads the external certmanager images to the local Toolforge repository for local consumption."""

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
            "--certmanager-version",
            required=True,
            type=validate_version,
            help="Version of certmanager to upgrade to (in N.N.N format).",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_common_opts(self.spicerack, args, CopyCertmanagerImagesToRepoRunner)(
            image_repo_url=args.image_repo_url,
            uploader_node=args.uploader_node,
            certmanager_version=args.certmanager_version,
            spicerack=self.spicerack,
        )


class CopyCertmanagerImagesToRepoRunner(WMCSCookbookRunnerBase):
    def __init__(
        self,
        common_opts: CommonOpts,
        image_repo_url: str,
        uploader_node: str,
        certmanager_version: str,
        spicerack: Spicerack,
    ):
        self.image_repo_url = image_repo_url
        self.uploader_node = uploader_node
        self.certmanager_version = certmanager_version
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for certmanager {self.certmanager_version}"

    def run(self) -> None:
        uploader_node = self.spicerack.remote().query(f"D{{{self.uploader_node}}}", use_sudo=True)
        image_ctrl = ImageController(spicerack=self.spicerack, uploader_node=uploader_node)

        for image in ["cainjector", "controller", "webhook", "startupapicheck"]:
            image_ctrl.update_image(
                pull_url=f"quay.io/jetstack/cert-manager-{image}:v{self.certmanager_version}",
                push_url=f"{self.image_repo_url}/cert-manager/{image}:v{self.certmanager_version}",
                log=False,
            )
