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

from wmcs_libs.ceph import CephClusterController, OSDTreeNode, OSDTreeOSDNode
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.ceph import CephClusterName

LOGGER = logging.getLogger(__name__)


class ShowInfo(CookbookBase):
    """WMCS Ceph cookbook to show some information on the osds in the cluster."""

    title = __doc__  # type: ignore

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
        # This is a read-only cookbook, we don't want to log to SAL
        args.no_dologmsg = True
        return with_common_opts(self.spicerack, args, ShowInfoRunner)(
            cluster_name=args.cluster_name,
            spicerack=self.spicerack,
        )


def _print_nested_nodes(node: OSDTreeNode, cur_indent: str = ""):
    if isinstance(node, OSDTreeOSDNode):
        print(f"{cur_indent}{node.name}({node.type}/{node.device_class}) {node.status} weight:{node.crush_weight}")
    else:
        print(f"{cur_indent}{node.name}({node.type})")
    for child in node.children:
        _print_nested_nodes(node=child, cur_indent=cur_indent + "    ")


def _print_stray(stray_nodes: list[dict[str, Any]]) -> None:
    # TODO: improve once we have an example
    print(f"stray: {stray_nodes}")


class ShowInfoRunner(WMCSCookbookRunnerBase):
    """Runner for ShowInfo"""

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
        _print_nested_nodes(node=osd_tree.root_node)
        _print_stray(osd_tree.stray)
