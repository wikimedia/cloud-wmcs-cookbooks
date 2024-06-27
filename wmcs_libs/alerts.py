#!/usr/bin/env python3
"""Alert and downtime related library functions and classes."""
from __future__ import annotations

import logging
from datetime import timedelta

from spicerack import Spicerack
from spicerack.alertmanager import MatchersType
from spicerack.icinga import IcingaError

from wmcs_libs.common import wrap_with_sudo_icinga

SilenceID = str

LOGGER = logging.getLogger(__name__)


def silence_host(
    spicerack: Spicerack,
    host_name: str,
    duration: timedelta = timedelta(hours=4),
    comment: str | None = None,
    task_id: str | None = None,
) -> SilenceID:
    """Silence a hosts alerts both in alertmanager and icinga.

    Examples of 'host_name':
    * cloudcontrol1005
    * cloudcephmon1001
    """
    reason = spicerack.admin_reason(reason=comment or "No comment", task_id=task_id)
    try:
        icinga_manager = wrap_with_sudo_icinga(spicerack).icinga_hosts(target_hosts=[host_name])
        icinga_manager.downtime(reason=reason)
    except IcingaError as error:
        if "not found" not in str(error):
            raise

    alertmanager_hosts = spicerack.alertmanager_hosts(target_hosts=[host_name])
    return alertmanager_hosts.downtime(reason=reason, duration=duration)


def silence_alert(
    spicerack: Spicerack,
    alert_name: str = "",
    duration: timedelta = timedelta(hours=1),
    comment: str = "no comment",
    task_id: str | None = None,
    extra_matchers: MatchersType | None = None,
) -> SilenceID:
    """Silence an alert, either by name and/or by the matchers passed.

    Examples of 'alert_name':
    * "Ceph Cluster Health"

    Example of matcher:
    * {"name": "service", "value": "~.*ceph.*", "isRegex": True}
    """
    matchers = list(matcher for matcher in extra_matchers or [])
    if alert_name:
        matchers.append({"name": "alert", "value": alert_name, "isRegex": False})

    alert_manager = spicerack.alertmanager()
    return alert_manager.downtime(
        reason=spicerack.admin_reason(reason=comment, task_id=task_id),
        matchers=matchers,
        duration=duration,
    )


def remove_silence(
    spicerack: Spicerack,
    silence_id: SilenceID | None = None,
    host_name: str | None = None,
) -> None:
    """Remove a silences and icinga acknowledgements (if hosts passed)."""

    # this is needed to be separate as sometimes we don't have the match silence-id/host, so we can't use
    # the `spicerack.alert_hosts().remove_downtime(silence_id)` function
    if host_name:
        try:
            icinga_manager = wrap_with_sudo_icinga(spicerack).icinga_hosts(target_hosts=[host_name])
            icinga_manager.remove_downtime()
        except IcingaError as error:
            if "not found" not in str(error):
                raise

    if silence_id:
        silence_manager = spicerack.alertmanager()
        silence_manager.remove_downtime(downtime_id=silence_id)
