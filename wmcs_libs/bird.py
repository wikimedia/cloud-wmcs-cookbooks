"""Classes and functions for interacting with the BIRD router daemon."""

from __future__ import annotations

from attr import dataclass
from spicerack.decorators import retry
from spicerack.remote import RemoteExecutionError

from wmcs_libs.common import (
    CUMIN_SAFE_WITH_OUTPUT,
    CommandRunnerMixin,
)


class BirdError(Exception):
    """Class for BIRD-related errors."""


@dataclass
class BirdProtocolStatus:
    """Represents the status data for a single protocol."""

    name: str
    protocol: str
    table: str | None
    state: str
    since: str
    info: str | None

    @classmethod
    def from_row(cls, row: str):
        columns = [entry for entry in row.split(" ") if entry != ""]
        return cls(
            name=columns[0],
            protocol=columns[1],
            table=columns[2] if columns[2] != "---" else None,
            state=columns[3],
            since=columns[4],
            info=columns[5] if len(columns) >= 6 else None,
        )


PROTOCOL_BGP = "BGP"
BGP_STATE_ESTABLISHED = "Established"


class Bird(CommandRunnerMixin):
    """Class to interact with a BIRD instance."""

    def _get_full_command(
        self, *command: str, json_output: bool = True, project_as_arg: bool = False, with_env_var: bool = True
    ):
        return ["birdc", *command]

    def get_protocol_status(self) -> list[BirdProtocolStatus]:
        # Skip headers: "Bird N.NN is ready" and the column name header row
        rows = self.run_raw("show", "protocol", cumin_params=CUMIN_SAFE_WITH_OUTPUT).strip().split("\n")[2:]
        return [BirdProtocolStatus.from_row(row) for row in rows]

    @retry(
        tries=10,
        backoff_mode="power",
        failure_message="BGP sessions not established",
        exceptions=(BirdError, RemoteExecutionError),
    )
    def ensure_bgp_established(self):
        """Check that all BGP sessions are in Established state."""

        for protocol in self.get_protocol_status():
            if protocol.protocol != PROTOCOL_BGP:
                continue
            if protocol.info == BGP_STATE_ESTABLISHED:
                continue
            raise BirdError(f"Protocol {protocol.name} is in '{protocol.info}' state")
