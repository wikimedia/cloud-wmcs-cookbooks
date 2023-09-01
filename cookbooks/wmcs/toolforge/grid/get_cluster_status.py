r"""WMCS Toolforge - grid - get cluster status

Usage example:
    cookbook wmcs.toolforge.grid.get_cluster_status \
        --project toolsbeta \
        --master-node-fqdn toolsbeta-test-etcd-8.toolsbeta.eqiad1.wikimedia.cloud
"""
from __future__ import annotations

import argparse
import logging

import yaml
from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.grid import GridController, GridQueueState, GridQueueStatesSet, GridQueueType, GridQueueTypesSet

LOGGER = logging.getLogger(__name__)


class NoAliasDumper(yaml.Dumper):  # pylint: disable=too-many-ancestors
    """Class override for the yaml module."""

    def ignore_aliases(self, data):
        """Function override, resolve yaml references."""
        return True


class ToolforgeGridGetClusterStatus(CookbookBase):
    """Toolforge cookbook to get the current grid cluster status"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--only-failed",
            required=False,
            action="store_true",
            help="If passed, will only show nodes and queues that are in failed status (that is, not OK).",
        )
        parser.add_argument(
            "--master-node-fqdn",
            required=False,
            default=None,
            help=(
                "Name of the grid master node, will use <project>-sgegrid-master.<project>.eqiad1.wikimedia.cloud by "
                "default."
            ),
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        # This is a read-only cookbook, we don't want to log to SAL
        args.no_dologmsg = True
        return with_common_opts(self.spicerack, args, ToolforgeGridGetClusterStatusRunner)(
            master_node_fqdn=args.master_node_fqdn
            or f"{args.project}-sgegrid-master.{args.project}.eqiad1.wikimedia.cloud",
            only_failed=args.only_failed,
            spicerack=self.spicerack,
        )


class ToolforgeGridGetClusterStatusRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeGridGetClusterStatus"""

    def __init__(
        self,
        common_opts: CommonOpts,
        master_node_fqdn: str,
        only_failed: bool,
        spicerack: Spicerack,
    ):
        """Init"""
        self.master_node_fqdn = master_node_fqdn
        self.project = common_opts.project
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.only_failed = only_failed
        self.grid_controller = GridController(remote=self.spicerack.remote(), master_node_fqdn=self.master_node_fqdn)

    def run(self) -> None:
        """Main entry point"""
        NoAliasDumper.add_representer(GridQueueType, GridQueueType.yaml_representer)
        NoAliasDumper.add_representer(GridQueueTypesSet, GridQueueTypesSet.yaml_representer)
        NoAliasDumper.add_representer(GridQueueState, GridQueueState.yaml_representer)
        NoAliasDumper.add_representer(GridQueueStatesSet, GridQueueStatesSet.yaml_representer)
        nodes_info = self.grid_controller.get_nodes_info()
        if self.only_failed:
            filtered_info = {
                node_name: node_info for node_name, node_info in nodes_info.items() if not node_info.is_ok()
            }
        else:
            filtered_info = nodes_info

        print("###### Nodes")
        print(yaml.dump(filtered_info, Dumper=NoAliasDumper))

        if not all(node_info.is_ok() for node_info in nodes_info.values()):
            print("###### Failed queues extended info")
            queue_infos = [
                queue_info for queue_info in self.grid_controller.get_queues_info() if not queue_info.is_ok()
            ]
            print(yaml.dump(queue_infos, Dumper=NoAliasDumper))

            print("###### Failed jobs logs")
            for queue_info in queue_infos:
                for job_id in queue_info.get_failed_jobs_from_message():
                    print(f"--- job:{job_id}")
                    print(self.grid_controller.get_job_error_logs(job_id=job_id))
                    print("-----------------")
