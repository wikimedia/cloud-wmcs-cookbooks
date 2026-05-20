"""Classes and functions for interacting with the BIRD router daemon."""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import timedelta

from attr import dataclass
from spicerack import Spicerack, SpicerackError
from spicerack.administrative import Reason
from spicerack.decorators import retry
from spicerack.remote import RemoteExecutionError, RemoteHosts

from wmcs_libs.common import (
    CUMIN_SAFE_WITH_OUTPUT,
    CommandRunnerMixin,
)

LOGGER = logging.getLogger(__name__)


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


@contextmanager
def bgp_downtimed(*, hosts: RemoteHosts, spicerack: Spicerack, reason: Reason, duration: timedelta):
    """Downtime BGP-related alerts on the given hosts."""

    try:
        am = spicerack.alertmanager()
    except SpicerackError as e:
        # Most likely this means we're running on a local setup
        LOGGER.info("Not downtiming alerts because Alertmanager is not available: %s", str(e))
        yield
        return

    silence_ids = []
    for host in hosts.split(len(hosts)):
        hostname, site, _ = str(host).split(".")
        cloud_private_fqdn = f"{hostname}.private.{site}.wikimedia.cloud"
        cloud_private_re = "|".join(re.escape(addr) for addr in spicerack.dns().resolve_ips(cloud_private_fqdn))

        # type hint to make mypy happy
        silences: list[list[dict[str, str | int | float | bool]]] = [
            # CoreBGPDown
            [
                {"name": "alertname", "value": "CoreBGPDown", "isRegex": False},
                {"name": "peer_descr", "value": hostname, "isRegex": False},
            ],
            # BFDdown
            [
                {"name": "alertname", "value": "BFDdown", "isRegex": False},
                {"name": "remote_address", "value": f"^({cloud_private_re})$", "isRegex": True},
            ],
        ]

        for matchers in silences:
            silence_id = am.downtime(
                reason=reason,
                duration=duration,
                matchers=matchers,
            )
            silence_ids.append(silence_id)

    yield

    for silence_id in silence_ids:
        am.remove_downtime(silence_id)
