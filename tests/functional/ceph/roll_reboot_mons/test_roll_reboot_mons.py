#!/usr/bin/env python3
from __future__ import annotations

from freezegun import freeze_time


def test_everything_goes_as_planned(run_cookbook_with_recording):
    run_result = run_cookbook_with_recording(
        record_file_name="everything_goes_ok.yaml",
        argv=[
            "wmcs.ceph.roll_reboot_mons",
            "--cluster-name=codfw1",
            "--no-dologmsg",
        ],
    )

    assert run_result.return_code == 0


def test_cluster_never_gets_healthy(run_cookbook_with_recording):
    with freeze_time(auto_tick_seconds=10):
        run_result = run_cookbook_with_recording(
            record_file_name="cluster_never_gets_healthy.yaml",
            argv=[
                "wmcs.ceph.roll_reboot_mons",
                "--cluster-name=codfw1",
                "--no-dologmsg",
            ],
        )

    assert run_result.return_code == 99
    assert (
        "wmcs_libs.ceph.CephClusterUnhealthy: Waited 1800 for the cluster to become healthy, "
        "but it never did, current state"
    ) in run_result.stderr


def test_manager_standby_never_comes_up(run_cookbook_with_recording):
    with freeze_time(auto_tick_seconds=10):
        run_result = run_cookbook_with_recording(
            record_file_name="manager_standby_never_comes_up.yaml",
            argv=[
                "wmcs.ceph.roll_reboot_mons",
                "--cluster-name=codfw1",
                "--no-dologmsg",
            ],
        )

    assert run_result.return_code == 99
    assert "Waited 600 for any manager to become standby, but it never did, current state" in run_result.stderr
