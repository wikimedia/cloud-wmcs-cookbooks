#!/usr/bin/env python3
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest
from spicerack._cookbook import main as run_cookbook

from wmcs_libs.test_helpers import ReplayError


@pytest.fixture(autouse=True)
def mock_side_effects():
    """Mock all the things that might contact hosts/external services aside from run_one_raw."""
    # multi-nested syntax needed for older python
    with patch("spicerack.remote.query"):
        with patch("spicerack.remote.RemoteHosts"):
            with patch("wmcs_libs.alerts.wrap_with_sudo_icinga"):
                with patch("wmcs_libs.ceph.time.sleep"):
                    yield


@pytest.fixture
def spicerack_config(tmp_path):
    """Fake spicerack config for cookbook tests."""
    cumin_config_path = tmp_path / "cumin.yaml"
    cookbooks_dir = Path(__file__).parent.parent.parent

    cumin_config_path.write_text(
        f"""transport: clustershell
log_file: {tmp_path}/cumin.log
default_backend: puppetdb

puppetdb:
    host: i.don.t.exist
    port: 443
    api_version: 4
    urllib3_disable_warnings:
      - SubjectAltNameWarning  # Temporary fix for T158757
        """
    )
    spicerack_config_path = tmp_path / "spicerack.yaml"
    spicerack_config_path.write_text(
        f"""
cookbooks_base_dirs:
- {cookbooks_dir}
logs_base_dir: {tmp_path}
instance_params:
  cumin_config: {cumin_config_path}
  spicerack_config_dir: {tmp_path}
        """
    )
    return spicerack_config_path


@dataclass(frozen=True)
class RunResult:
    return_code: None | int
    stdout: str
    stderr: str


@pytest.fixture
def run_cookbook_with_recording(request, capsys, spicerack_config):
    """Gives a function to run a cookbook with the given record name.

    The recording must live in a directory called `recordings` in same directory as the test being run.

    It will raise ReplayError if not all the lines in the record were replayed.

    Use like:

    > def test_my_cookbook(spicerack_config, run_cookbook_with_recording):
    >   res = run_cookbook_with_recording(
            record_file_name="cluster_never_gets_healthy.yaml",
    >       argv=[
    >           "wmcs.ceph.roll_reboot_mons",
    >           "--cluster-name=codfw1",
    >           "--no-dologmsg",
    >       ]
    >   )
    >
    >   assert res.return_code == 0
    >   assert "I did something" in res.stdout
    >   assert "An error happened" not in res.stderr
    """

    def _inner_run_with_recordings(record_file_name: str, argv: List[str]) -> RunResult:
        record_file_path = Path(request.module.__file__).parent / "recordings" / record_file_name
        with patch.dict(
            os.environ,
            {
                "COOKBOOK_REPLAYING_ENABLED": "yes",
                "COOKBOOK_RECORDING_FILE": str(record_file_path),
            },
        ):
            return_code = run_cookbook(argv=[f"--config={spicerack_config}"] + argv)

        captured = capsys.readouterr()

        # ugly hack to re-raise the replay error, as run_cookbook swallows the exception
        replay_error_regex = re.compile(
            r"wmcs_libs\.test_helpers\.ReplayError: Not all the entries in the record.*were replayed.*\n"
        )
        match = replay_error_regex.search(captured.err)
        if match:
            raise ReplayError(match.group().split(": ", 1)[-1])

        return RunResult(
            return_code=return_code,
            stdout=captured.out,
            stderr=captured.err,
        )

    return _inner_run_with_recordings
