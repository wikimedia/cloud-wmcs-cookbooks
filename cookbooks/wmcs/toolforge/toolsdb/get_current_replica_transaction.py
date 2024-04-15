r"""WMCS Toolforge - ToolsDB - get cluster status

Usage example:
    cookbook wmcs.toolforge.toolsdb.get_current_replica_transaction
"""

from __future__ import annotations

import argparse
import logging
import re
from typing import cast

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.toolsdb import ToolforgeToolsDBClusterName
from wmcs_libs.toolsdb import ReplicaReplicationState, ToolsDBController

LOGGER = logging.getLogger(__name__)


class ToolsDBGetCurrentReplicaTransaction(CookbookBase):
    """Toolforge cookbook to get the current toolsdb cluster status"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument(
            "--cluster-name",
            required=False,
            choices=list(ToolforgeToolsDBClusterName),
            type=ToolforgeToolsDBClusterName,
            default=ToolforgeToolsDBClusterName.TOOLS,
            help="cluster to work on",
        )
        parser.add_argument(
            "--show-raw-binlog",
            required=False,
            action="store_true",
            help="If set, will show also the relevant section of the raw binlog.",
        )
        add_common_opts(parser)

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        # This is a read-only cookbook, we don't want to log to SAL
        args.no_dologmsg = True
        return with_common_opts(self.spicerack, args, ToolsDBGetCurrentReplicaTransactionRunner)(
            spicerack=self.spicerack,
            cluster_name=args.cluster_name,
            show_raw_binlog=args.show_raw_binlog,
        )


class ToolsDBGetCurrentReplicaTransactionRunner(WMCSCookbookRunnerBase):
    """Runner for ToolsDBGetCurrentReplicaTransaction"""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeToolsDBClusterName,
        show_raw_binlog: bool,
        spicerack: Spicerack,
    ):
        """Init"""
        self.project = common_opts.project
        self.show_raw_binlog = show_raw_binlog
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.toolsdb_controller = ToolsDBController(remote=self.spicerack.remote(), cluster_name=cluster_name)

    def run(self) -> None:
        """Main entry point"""
        replica_nodes = self.toolsdb_controller.get_replica_nodes()

        # TODO: allow filtering which replica to get it from, once we have >1
        myreplica = next(node for node in replica_nodes.values())
        replication_state = cast(ReplicaReplicationState, myreplica.get_replication_state())

        start = int(replication_state.exec_master_log_pos)
        new_stop = start + 1
        last_stop = new_stop
        binlog_chunk = ""
        maybe_table_regex = re.compile("Table_map: ([^ ]*) mapped")
        maybe_table_match = None
        # we keep going until we find some useful data
        while not maybe_table_match:
            binlog_chunk = self.toolsdb_controller.primary_node.get_binlog_entry(
                logfile=replication_state.relay_master_log_file,
                # we start at the same place, as starting from a non-existing position will make the command fail
                # and the numbers are not sequential
                start_pos=start,
                stop_pos=new_stop,
            )
            maybe_table_match = maybe_table_regex.search(binlog_chunk)
            new_stop = last_stop + 10000
            last_stop = new_stop

        potential_tables = set()
        potential_queries = set()
        for binlog_line in binlog_chunk.splitlines():
            maybe_table_match = maybe_table_regex.search(binlog_line)
            if maybe_table_match:
                potential_tables.add(maybe_table_match.group())

            if binlog_line.startswith("#Q>"):
                potential_queries.add(binlog_line)

        potential_queries_str = "\n    ".join(potential_queries)
        potential_tables_str = "\n    ".join(potential_tables)
        print(f"Suspicious tables:\n    {potential_tables_str}")
        print(f"Suspicious queries:\n    {potential_queries_str}")
        if self.show_raw_binlog:
            print("Raw logs:\n------------")
            print(binlog_chunk)
            print("------------")
