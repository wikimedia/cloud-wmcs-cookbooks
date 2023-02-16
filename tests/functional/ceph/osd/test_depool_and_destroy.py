#!/usr/bin/env python3
from __future__ import annotations

import re

from freezegun import freeze_time

ALL_REMOVED_REGEX = re.compile(
    "Depooled and destroyed OSD daemons (?P<osds>.*) and removed the OSD host (?P<host>.*) from the CRUSH map."
)


def test_all_osds_and_host_removed(spicerack_config, run_cookbook_with_recording):
    with freeze_time(auto_tick_seconds=10):
        run_result = run_cookbook_with_recording(
            record_file_name="everything_goes_ok_all_osds.yaml",
            argv=[
                f"--config-file={spicerack_config}",
                "wmcs.ceph.osd.depool_and_destroy",
                "--no-dologmsg",
                "--yes-i-know-what-im-doing",
                "--all-osds",
                "--osd-hostname=cloudcephosd1004",
            ],
        )

    assert run_result.return_code == 0
    match = ALL_REMOVED_REGEX.search(run_result.stderr)
    assert match
