r"""WMCS Ceph - Show information about the osds in the cluster

Usage example:
    cookbook wmcs.ceph.osd.show_info \
        --cluster-name eqiad1

"""
from __future__ import annotations

import argparse
import logging
from typing import Any

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.ceph import CephClusterController
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory import CephClusterName

LOGGER = logging.getLogger(__name__)


class ShowInfo(CookbookBase):
    """WMCS Ceph cookbook to show some information on the osds in the cluster."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(CephClusterName),
            type=CephClusterName,
            help="Ceph cluster to show information for.",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, ShowInfoRunner)(
            cluster_name=args.cluster_name,
            spicerack=self.spicerack,
        )


def _print_nodes(nodes_tree: dict[str, Any]) -> None:
    # we expect a tree with one single root node from ceph
    print("root:")
    for node in sorted(nodes_tree["children"], key=lambda x: x["name"]):
        print(f"  {node['name']}(type:{node['type']})")
        for osd in sorted(node["children"], key=lambda x: x.osd_id):
            print(f"    {osd.name}(class:{osd.device_class}) {osd.status} weight:{osd.crush_weight}")


def _print_stray(stray_nodes: list[dict[str, Any]]) -> None:
    # TODO: improve once we have an example
    print(f"stray: {stray_nodes}")


class ShowInfoRunner(WMCSCookbookRunnerBase):
    """Runner for BootstrapAndAdd"""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: CephClusterName,
        spicerack: Spicerack,
    ):
        """Init"""
        self.cluster_controller = CephClusterController(
            remote=spicerack.remote(), cluster_name=cluster_name, spicerack=spicerack
        )
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    def run(self) -> None:
        """Main entry point"""
        osd_tree = self.cluster_controller.get_osd_tree()
        _print_nodes(osd_tree.get("nodes", {}))
        _print_stray(osd_tree.get("stray", {}))
