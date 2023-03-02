#!/usr/bin/env python3
from __future__ import annotations


def test_everything_goes_as_planned(run_cookbook_with_recording):
    run_result = run_cookbook_with_recording(
        record_file_name="everything_goes_ok.yaml",
        argv=[
            "wmcs.toolforge.k8s.component.deploy",
            "--git-url=https://github.com/toolforge/buildpack-admission-controller",
            "--no-dologmsg",
            "--cluster-name=toolsbeta",
        ],
    )

    assert run_result.return_code == 0
