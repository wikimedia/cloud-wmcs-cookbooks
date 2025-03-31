from __future__ import annotations

from unittest.mock import ANY

import pytest
from spicerack.administrative import Reason

from wmcs_libs.alerts import silence_host
from wmcs_libs.common import UtilsForTesting


def get_stub_silence(silence_id: str):
    return {
        "id": silence_id,
        "status": {"state": "active"},
        "updatedAt": "2022-07-01T16:08:26.754Z",
        "comment": "ACK! This alert was acknowledged using karma on Tue, 14 Jun 2022 08:29:09 GMT",
        "createdBy": "some_user",
        "endsAt": "2022-07-31T16:19:00.000Z",
        "matchers": [{"isRegex": False, "name": "team", "value": "wmcs"}],
        "startsAt": "2022-07-01T16:08:26.754Z",
    }


@pytest.fixture()
def spicerack(monkeypatch):
    fake_spicerack = UtilsForTesting.get_fake_spicerack(UtilsForTesting.get_fake_remote())
    # needed for icinga_hosts
    fake_spicerack._dry_run = False
    fake_spicerack.icinga_master_host.return_value.__len__.return_value = 1
    return fake_spicerack


def test_silence_host_passes_hostname(spicerack):
    expected_hostname = "testhost1"
    spicerack.admin_reason.return_value = Reason(reason="doing tests", username="testuser", hostname=expected_hostname)
    spicerack.alertmanager_hosts.return_value.downtime.return_value = "silly silence"

    silence_host(spicerack=spicerack, host_name=expected_hostname, task_id="T12345", comment="silly comment")

    spicerack.alertmanager_hosts.assert_called_with(target_hosts=[expected_hostname])


def test_silence_host_passes_task_id(spicerack):
    expected_task_id = "T12345"
    spicerack.admin_reason.return_value = Reason(reason="doing tests", username="testuser", hostname="testhost1")
    spicerack.alertmanager_hosts.return_value.downtime.return_value = "silly silence"

    silence_host(spicerack=spicerack, host_name="testhost1", task_id=expected_task_id, comment="silly comment")

    spicerack.admin_reason.assert_called_with(reason=ANY, task_id=expected_task_id)


def test_silence_host_passes_comment(spicerack):
    expected_reason = Reason(reason="doing tests", username="testuser", hostname="testhost1")
    spicerack.admin_reason.return_value = expected_reason
    spicerack.alertmanager_hosts.return_value.downtime.return_value = "silly silence"

    silence_host(spicerack=spicerack, host_name="testhost1", task_id="T12345", comment="doing tests")

    spicerack.alertmanager_hosts.return_value.downtime.assert_called_with(reason=expected_reason, duration=ANY)
