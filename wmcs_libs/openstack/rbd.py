#!/usr/bin/env python3
"""Openstack Neutron specific related code."""
from __future__ import annotations

import logging

from wmcs_libs.common import CUMIN_SAFE_WITHOUT_OUTPUT, CommandRunnerMixin
from wmcs_libs.openstack.common import OpenstackClusterName, OpenstackIdentifier, Remote, get_control_nodes

LOGGER = logging.getLogger(__name__)


class RBDRunner(CommandRunnerMixin):
    """Class to interact with the RBD commandline"""

    def __init__(
        self,
        remote: Remote,
        pool_name: str,
        cluster_name: OpenstackClusterName = OpenstackClusterName.EQIAD1,
    ):
        """Init."""
        self.cluster_name = cluster_name
        self.pool_name = pool_name
        self.control_node_fqdn = get_control_nodes(cluster_name)[0]
        self.control_node = remote.query(f"D{{{self.control_node_fqdn}}}", use_sudo=True)
        super().__init__(command_runner_node=self.control_node)

    def _get_full_command(
        self, *command: str, json_output: bool = True, project_as_arg: bool = False, with_env_var: bool = True
    ):
        # some commands don't have formatted output
        if json_output:
            format_args = ["-f", "json"]
        else:
            format_args = []
        if "delete" in command:
            format_args = []

        return ["rbd", "--pool", self.pool_name, *command, *format_args]

    def purge_server_snapshots(self, server_id: OpenstackIdentifier) -> None:
        """Rebuild db instance."""
        self.run_raw("snap", "purge", f"{server_id}_disk", cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT, json_output=False)
