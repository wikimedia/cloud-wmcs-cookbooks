r"""WMCS Toolforge - Upload the needed container images for kyverno to the toolforge container image registry

Usage example:
    cookbook wmcs.toolforge.k8s.kyverno.copy_images_to_registry
"""

from __future__ import annotations

import argparse

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.k8s.images import ImageController

IMAGES = {
    "ghcr.io/kyverno/kyverno:{kyverno_version}": "toolforge-kyverno-kyverno:{kyverno_version}",
    "ghcr.io/kyverno/kyverno-cli:{kyverno_version}": "toolforge-kyverno-kyverno-cli:{kyverno_version}",
    "ghcr.io/kyverno/kyvernopre:{kyverno_version}": "toolforge-kyverno-kyvernopre:{kyverno_version}",
    "ghcr.io/kyverno/background-controller:{kyverno_version}": (
        "toolforge-kyverno-background-controller:{kyverno_version}"
    ),
    "ghcr.io/kyverno/cleanup-controller:{kyverno_version}": "toolforge-kyverno-cleanup-controller:{kyverno_version}",
    "ghcr.io/kyverno/reports-controller:{kyverno_version}": "toolforge-kyverno-reports-controller:{kyverno_version}",
    "bitnami/kubectl:{kubectl_version}": "bitnami-kubectl:{kubectl_version}",
    "busybox:{busybox_version}": "busybox:{busybox_version}",
}


class CopyImagesToRepo(CookbookBase):
    """Uploads the external kyverno images to the local toolforge repository for local comsumption."""

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
        parser.add_argument(
            "--busybox-version",
            required=False,
            default="1.35",
            help="Version of busybox image to upgrade to (matches the image tag).",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(self.spicerack, args, CopyImagesToRepoRunner)(
            image_repo_url=args.image_repo_url,
            uploader_node=args.uploader_node,
            kyverno_version=args.kyverno_version,
            bitnami_kubectl_version=args.bitnami_kubectl_version,
            busybox_version=args.busybox_version,
            spicerack=self.spicerack,
        )


class CopyImagesToRepoRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        image_repo_url: str,
        uploader_node: str,
        kyverno_version: str,
        bitnami_kubectl_version: str,
        busybox_version: str,
        spicerack: Spicerack,
    ):

        self.image_repo_url = image_repo_url
        self.uploader_node = uploader_node
        self.kyverno_version = kyverno_version
        self.bitnami_kubectl_version = bitnami_kubectl_version
        self.busybox_version = busybox_version
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run(self) -> None:

        remote = self.spicerack.remote()
        uploader_node = remote.query(f"D{{{self.uploader_node}}}", use_sudo=True)
        image_ctrl = ImageController(spicerack=self.spicerack, uploader_node=uploader_node)

        pushed_images = []
        for pull_url, push_url in IMAGES.items():
            push_url = (
                self.image_repo_url
                + "/"
                + push_url.format(
                    kyverno_version=self.kyverno_version,
                    kubectl_version=self.bitnami_kubectl_version,
                    busybox_version=self.busybox_version,
                )
            )
            image_ctrl.update_image(
                pull_url=pull_url.format(
                    kyverno_version=self.kyverno_version,
                    kubectl_version=self.bitnami_kubectl_version,
                    busybox_version=self.busybox_version,
                ),
                push_url=push_url,
            )
            pushed_images.append(push_url)

        print("Updated all the kyverno-specific images:")
        print("    " + "\n    ".join(pushed_images))
