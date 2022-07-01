#!/usr/bin/env python3
"""Alert and downtime related library functions and classes."""
import getpass
import logging
import socket
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from spicerack import Spicerack
from spicerack.remote import Remote, RemoteHosts

from cookbooks.wmcs import run_one_formatted_as_list, run_one_raw, wrap_with_sudo_icinga

SilenceID = str

ALERTMANAGER_HOST = "alert1001.wikimedia.org"
LOGGER = logging.getLogger(__name__)


@dataclass
class AlertManager:
    """Class to handle alert manager silences."""

    node: RemoteHosts

    @classmethod
    def from_remote(cls, remote: Remote) -> "AlertManager":
        """Get an AlertManager instance from a remote."""
        node = remote.query(f"D{{{ALERTMANAGER_HOST}}}")
        return cls(node=node)

    def get_silences(self, query: str) -> List[Dict[str, Any]]:
        """Get all silences enabled filtering with query.

        Some examples of 'query':
        * alertname=foo
        * instance=bar
        * alertname=~.*foo.*
        """
        return run_one_formatted_as_list(node=self.node, command=["amtool", "--output=json", "silence", "query", query])

    def downtime_alert(self, alert_name: str, comment: str, duration: Optional[str] = None) -> SilenceID:
        """Add a silence for an alert.

        Examples of 'alert_name':
        * "Ceph Cluster Health"

        Examples of 'duration':
        * 1h -> one hour
        * 2d -> two days
        """
        command = [
            "amtool",
            "--output=json",
            "silence",
            "add",
            f'--duration="{duration or "1h"}"',
            f"--comment='{comment}'",
            f"alertname={alert_name}",
        ]
        return run_one_raw(node=self.node, command=command)

    def uptime_alert(self, alert_name: str) -> None:
        """Remove a silence for an alert.

        Examples of 'alert_name':
        * "Ceph Cluster Health"
        """
        existing_silences = self.get_silences(query=f"alertname={alert_name}")
        to_expire = [silence["id"] for silence in existing_silences]

        if not to_expire:
            LOGGER.info("No silences for 'alertname=%s' found.", alert_name)
            return

        command = [
            "amtool",
            "--output=json",
            "silence",
            "expire",
            f"alertname={alert_name}",
        ]
        run_one_raw(node=self.node, command=command)

    def downtime_host(self, host_name: str, comment: str, duration: Optional[str] = None) -> SilenceID:
        """Add a silence for a host.

        Examples of 'host_name':
        * cloudcontrol1003
        * cloudcephmon1001

        Examples of 'duration':
        * 1h -> one hour
        * 2d -> two days
        """
        command = [
            "amtool",
            "--output=json",
            "silence",
            "add",
            f'--duration="{duration or "1h"}"',
            f"--comment='{comment}'",
            f"instance={host_name}",
        ]
        return run_one_raw(node=self.node, command=command)

    def expire_silence(self, silence_id: str) -> None:
        """Expire a silence."""
        command = [
            "amtool",
            "--output=json",
            "silence",
            "expire",
            silence_id,
        ]
        run_one_raw(node=self.node, command=command)

    def uptime_host(self, host_name: str) -> None:
        """Expire all silences for a host."""
        existing_silences = self.get_silences(query=f"instance={host_name}")
        to_expire = [silence["id"] for silence in existing_silences]

        if not to_expire:
            LOGGER.info("No silences for 'instance=%s' found.", host_name)
            return

        command = [
            "amtool",
            "--output=json",
            "silence",
            "expire",
        ] + to_expire
        run_one_raw(node=self.node, command=command)


def downtime_host(
    spicerack: Spicerack,
    host_name: str,
    duration: Optional[str] = None,
    comment: Optional[str] = None,
    task_id: Optional[str] = None,
) -> SilenceID:
    """Do whatever it takes to downtime a host.

    Examples of 'host_name':
    * cloudcontrol1003
    * cloudcephmon1001

    Examples of 'duration':
    * 1h -> one hour
    * 2d -> two days
    """
    postfix = f"- from cookbook ran by {getpass.getuser()}@{socket.gethostname()}"
    if task_id:
        postfix = f" ({task_id}) {postfix}"
    if comment:
        final_comment = comment + postfix
    else:
        final_comment = "No comment" + postfix

    alert_manager = AlertManager.from_remote(spicerack.remote())
    silence_id = alert_manager.downtime_host(host_name=host_name, duration=duration, comment=final_comment)

    icinga_hosts = wrap_with_sudo_icinga(my_spicerack=spicerack).icinga_hosts(target_hosts=[host_name])
    icinga_hosts.downtime(reason=spicerack.admin_reason(reason=comment or "No comment", task_id=task_id))

    return silence_id


def uptime_host(spicerack: Spicerack, host_name: str, silence_id: Optional[SilenceID] = None) -> None:
    """Do whatever it takes to uptime a host, if silence_id passed, only that silence will be expired.

    Examples of 'host_name':
    * cloudcontrol1003
    * cloudcephmon1001
    """
    alert_manager = AlertManager.from_remote(spicerack.remote())
    if silence_id:
        alert_manager.expire_silence(silence_id=silence_id)
    else:
        alert_manager.uptime_host(host_name=host_name)

    icinga_hosts = wrap_with_sudo_icinga(my_spicerack=spicerack).icinga_hosts(target_hosts=[host_name])
    icinga_hosts.remove_downtime()


def downtime_alert(
    spicerack: Spicerack,
    alert_name: str,
    duration: Optional[str] = None,
    comment: Optional[str] = None,
    task_id: Optional[str] = None,
) -> SilenceID:
    """Do whatever it takes to downtime a host.

    Examples of 'alert_name':
    * "Ceph Cluster Health"

    Examples of 'duration':
    * 1h -> one hour
    * 2d -> two days
    """
    postfix = f"- from cookbook ran by {getpass.getuser()}@{socket.gethostname()}"
    if task_id:
        postfix = f" ({task_id}) {postfix}"
    if comment:
        final_comment = comment + postfix
    else:
        final_comment = "No comment" + postfix

    alert_manager = AlertManager.from_remote(spicerack.remote())
    return alert_manager.downtime_alert(alert_name=alert_name, duration=duration, comment=final_comment)


def uptime_alert(
    spicerack: Spicerack, alert_name: Optional[str] = None, silence_id: Optional[SilenceID] = None
) -> None:
    """Do whatever it takes to uptime an alert, if silence_id passed, only that silence will be expired.

    Examples of 'alert_name':
    * "Ceph Cluster Health"
    """
    alert_manager = AlertManager.from_remote(spicerack.remote())
    if silence_id:
        alert_manager.expire_silence(silence_id=silence_id)
    elif alert_name:
        alert_manager.uptime_alert(alert_name=alert_name)
    else:
        raise ValueError("You must pass either silence_id or alert_name")
