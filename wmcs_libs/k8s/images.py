from spicerack import Spicerack
from spicerack.remote import RemoteHosts

from ..common import run_one_raw


class ImageController:
    """Controller for Toolforge k8s container images."""

    def __init__(self, spicerack: Spicerack, uploader_node: RemoteHosts):
        self.spicerack = spicerack
        self.uploader_node = uploader_node

    def update_image(self, pull_url: str, push_url: str) -> str:
        self.spicerack.sal_logger.info("Updating container image %s", push_url)
        run_one_raw(command=["docker", "pull", pull_url], node=self.uploader_node)
        run_one_raw(command=["docker", "tag", pull_url, push_url], node=self.uploader_node)
        return run_one_raw(command=["docker", "push", push_url], node=self.uploader_node)
