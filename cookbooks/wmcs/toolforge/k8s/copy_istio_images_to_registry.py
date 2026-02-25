r"""WMCS Toolforge - Upload the Istio images to the Toolforge container image registry

Usage example:
    cookbook wmcs.toolforge.k8s.copy_istio_images_to_registry --istio-version N.N.N
"""

import argparse

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.k8s.images import ImageController
from wmcs_libs.k8s.kubernetes import validate_version


class CopyIstioImagesToRepo(CookbookBase):
    """Uploads the external Istio images to the local Toolforge repository for local consumption."""

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
            "--istio-version",
            required=True,
            type=validate_version,
            help="Version of Istio to upgrade to (in N.N.N format).",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_common_opts(self.spicerack, args, CopyIstioImagesToRepoRunner)(
            image_repo_url=args.image_repo_url,
            uploader_node=args.uploader_node,
            istio_version=args.istio_version,
            spicerack=self.spicerack,
        )


class CopyIstioImagesToRepoRunner(WMCSCookbookRunnerBase):
    def __init__(
        self,
        common_opts: CommonOpts,
        image_repo_url: str,
        uploader_node: str,
        istio_version: str,
        spicerack: Spicerack,
    ):
        self.image_repo_url = image_repo_url
        self.uploader_node = uploader_node
        self.istio_version = istio_version
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for Istio {self.istio_version}"

    def run(self) -> None:
        uploader_node = self.spicerack.remote().query(f"D{{{self.uploader_node}}}", use_sudo=True)
        image_ctrl = ImageController(spicerack=self.spicerack, uploader_node=uploader_node)

        for image in ["pilot", "proxyv2"]:
            image_ctrl.update_image(
                pull_url=f"docker.io/istio/{image}:{self.istio_version}",
                push_url=f"{self.image_repo_url}/istio/{image}:{self.istio_version}",
                log=False,
            )
