"""Library for manipulating Puppet ENC data."""
from typing import Any, ClassVar, Dict

import yaml
from spicerack import Remote, RemoteHosts

from wmcs_libs.common import (
    CUMIN_SAFE_WITHOUT_OUTPUT,
    CUMIN_UNSAFE_WITH_OUTPUT,
    CUMIN_UNSAFE_WITHOUT_OUTPUT,
    CommandRunnerMixin,
    OutputFormat,
    with_temporary_file,
)
from wmcs_libs.inventory import OpenstackClusterName
from wmcs_libs.openstack.common import get_control_nodes


class EncPrefix(CommandRunnerMixin):
    """Represents a single prefix."""

    def __init__(self, command_runner_node: RemoteHosts, project_id: str, prefix_name: str):
        """Init."""
        super().__init__(command_runner_node)
        self.project_id = project_id
        self.prefix_name = prefix_name

    def _get_full_command(self, *command: str, json_output: bool = True, project_as_arg: bool = False):
        return [
            "wmcs-enc-cli",
            *(["--openstack-project", self.project_id] if project_as_arg else []),
            *command,
        ]

    def get_current_hiera(self) -> Dict[str, Any]:
        """Retrieves the current hieradata."""
        result = self.run_formatted_as_dict(
            "get_prefix_hiera",
            self.prefix_name,
            project_as_arg=True,
            try_format=OutputFormat.YAML,
            cumin_params=CUMIN_SAFE_WITHOUT_OUTPUT,
        )

        return yaml.safe_load(result["hiera"])

    def replace_hiera(self, hiera: Dict[str, Any]) -> None:
        """Replaces the hieradata with the given argument."""
        with with_temporary_file(
            dst_node=self.command_runner_node, contents=yaml.safe_dump(hiera), cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT
        ) as file_name:
            self.run_formatted_as_dict(
                "set_prefix_hiera",
                self.prefix_name,
                file_name,
                project_as_arg=True,
                try_format=OutputFormat.YAML,
                cumin_params=CUMIN_UNSAFE_WITH_OUTPUT,
            )

    def set_hiera_values(self, values: Dict[str, Any]) -> None:
        """Updates the hieradata with the given values, leaving everything else as is."""
        hiera = self.get_current_hiera()
        hiera.update(values)
        self.replace_hiera(hiera)


class Enc:
    """Class to interact with ENC in a specific OpenStack deployment."""

    PROJECT_PREFIX: ClassVar[str] = "_"
    """The prefix name that applies to the entire project."""

    def __init__(
        self,
        remote: Remote,
        cluster_name: OpenstackClusterName = OpenstackClusterName.EQIAD1,
    ):
        """Init."""
        control_node_fqdn = get_control_nodes(cluster_name)[0]
        self.control_node = remote.query(f"D{{{control_node_fqdn}}}", use_sudo=True)

    def prefix(self, project_id: str, prefix_name: str) -> EncPrefix:
        """Gets a EncPrefix object to interact with a specific prefix."""
        return EncPrefix(command_runner_node=self.control_node, project_id=project_id, prefix_name=prefix_name)
