r"""WMCS Toolforge - Upload the needed container images for kyverno to the toolforge container image registry

Usage example:
    cookbook wmcs.toolforge.k8s.kyverno.copy_images_to_registry
"""

import argparse

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.k8s.images import ImageController


class CopyImagesToRepo(CookbookBase):
    """Uploads the external kyverno images to the local toolforge repository for local comsumption."""

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
            "--image-repo-url",
            required=False,
            default="docker-registry.tools.wmflabs.org",
            help="Repository to upload the images to.",
        )
        parser.add_argument(
            "--uploader-node",
            required=False,
            default="tools-imagebuilder-2.tools.eqiad1.wikimedia.cloud",
            help="Host to use to pull and push to the given repository.",
        )
        parser.add_argument(
            "--kyverno-version",
            required=False,
            default="v1.12.5",
            help="Version of kyverno to upgrade to (matches the image tag).",
        )
        parser.add_argument(
            "--bitnami-kubectl-version",
            required=False,
            default="1.28.5",
            help="Version of bitname/kubectl image to upgrade to (matches the image tag).",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, CopyImagesToRepoRunner)(
            image_repo_url=args.image_repo_url,
            uploader_node=args.uploader_node,
            kyverno_version=args.kyverno_version,
            bitnami_kubectl_version=args.bitnami_kubectl_version,
            spicerack=self.spicerack,
        )


class CopyImagesToRepoRunner(WMCSCookbookRunnerBase):
    """Runner for CopyImagesToRepo."""

    KYVERNO_IMAGE_BASE_URL = "ghcr.io/kyverno/"
    KYVERNO_IMAGES_NAMES = [
        "kyverno",
        "kyvernopre",
        "background-controller",
        "cleanup-controller",
        "reports-controller",
    ]

    KYVERNO_KUBECTL_PULL = "bitnami/kubectl"
    KYVERNO_KUBECTL_PUSH_NAME = "bitnami-kubectl"

    def __init__(
        self,
        common_opts: CommonOpts,
        image_repo_url: str,
        uploader_node: str,
        kyverno_version: str,
        bitnami_kubectl_version: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.image_repo_url = image_repo_url
        self.uploader_node = uploader_node
        self.kyverno_version = kyverno_version
        self.bitnami_kubectl_version = bitnami_kubectl_version
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run(self) -> None:
        """Main entry point"""
        remote = self.spicerack.remote()
        uploader_node = remote.query(f"D{{{self.uploader_node}}}", use_sudo=True)
        image_ctrl = ImageController(spicerack=self.spicerack, uploader_node=uploader_node)

        for image in self.KYVERNO_IMAGES_NAMES:
            pull_url = f"{self.KYVERNO_IMAGE_BASE_URL}{image}:{self.kyverno_version}"
            push_url = f"{self.image_repo_url}/toolforge-kyverno-{image}:{self.kyverno_version}"
            image_ctrl.update_image(pull_url=pull_url, push_url=push_url)

        pull_url = f"{self.KYVERNO_KUBECTL_PULL}:{self.bitnami_kubectl_version}"
        push_url = f"{self.image_repo_url}/{self.KYVERNO_KUBECTL_PUSH_NAME}:{self.bitnami_kubectl_version}"
        image_ctrl.update_image(pull_url=pull_url, push_url=push_url)
