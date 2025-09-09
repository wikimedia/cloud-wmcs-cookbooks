#!/usr/bin/env python3


def test_deploy_package(run_cookbook_with_recording, monkeypatch):
    monkeypatch.setattr(
        "wmcs_libs.wm_gitlab.GitlabController.get_artifact_urls", lambda *args, **kwargs: ["http://silly.url.local"]
    )
    run_result = run_cookbook_with_recording(
        record_file_name="deploy_package.yaml",
        argv=[
            "wmcs.toolforge.component.deploy",
            "--component=builds-cli",
            "--git-branch=bump_to_0.0.18",
            "--no-dologmsg",
            "--cluster-name=toolsbeta",
            "--skip-tests",
        ],
    )

    assert run_result.return_code == 0


def test_deploy_component(run_cookbook_with_recording):
    run_result = run_cookbook_with_recording(
        record_file_name="deploy_k8s_component.yaml",
        argv=[
            "wmcs.toolforge.component.deploy",
            "--component=builds-builder",
            "--git-branch=bump_builds-builder",
            "--no-dologmsg",
            "--cluster-name=toolsbeta",
            "--skip-tests",
        ],
    )

    assert run_result.return_code == 0
