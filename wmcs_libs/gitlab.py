#!/usr/bin/env python3
from __future__ import annotations

import time
from typing import Any, cast

import gitlab as upstream_gitlab_lib
import requests

GITLAB_BASE_URL = "https://gitlab.wikimedia.org"
GITLAB_API_BASE_URL = f"{GITLAB_BASE_URL}/api/v4"
PACKAGE_JOB_NAME = "package:deb"
# Gotten from the gitlab group page
TOOLFORGE_GROUP_ID = 203
CLI_TO_PACKAGE_NAME = {
    "jobs-cli": "toolforge-jobs-framework-cli",
    "tools-webservice": "toolforge-webservice",
    "envvars-cli": "toolforge-envvars-cli",
    "builds-cli": "toolforge-builds-cli",
    "toolforge-cli": "toolforge-cli",
}


def _do_get_dict(path: str, **kwargs) -> dict[str, Any]:
    if not path.startswith("http"):
        path = f"{GITLAB_API_BASE_URL}{path}"

    response = requests.get(path, verify=False, timeout=10, **kwargs)  # nosec B501
    response.raise_for_status()
    return response.json()


def _do_get_list(path: str, **kwargs) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _do_get_dict(path=path, **kwargs))


def get_package_job(project: dict[str, Any], pipeline: dict[str, Any]) -> dict[str, Any]:
    for job in _do_get_list(f"/projects/{project['id']}/pipelines/{pipeline['id']}/jobs"):
        if job["name"] == PACKAGE_JOB_NAME:
            return job

    raise Exception(f"Unable to find a package job({PACKAGE_JOB_NAME}) in pipeline {pipeline['web_url']}")


def get_mr(project: dict[str, Any], mr_number: int) -> dict[str, Any]:
    return _do_get_dict(f"/projects/{project['id']}/merge_requests/{mr_number}")


def get_mrs(project: dict[str, Any]) -> list[dict[str, Any]]:
    return _do_get_list(f"/projects/{project['id']}/merge_requests?state=opened")


def get_last_pipeline(project: dict[str, Any], mr_number: int) -> dict[str, Any]:
    mr_data = get_mr(project=project, mr_number=mr_number)
    while mr_data["head_pipeline"]["status"] == "running":
        print(f"Pipeline {mr_data['head_pipeline']['iid']} is still running, waiting for it to finish....")
        time.sleep(10)
        mr_data = get_mr(project=project, mr_number=mr_number)

    if mr_data["head_pipeline"]["status"] != "success":
        raise Exception(
            f"Unable to find a successful pipeline for MR {mr_number} ({mr_data['web_url']}), last pipeline status: "
            f"{mr_data['head_pipeline']['status']}"
        )

    return mr_data["head_pipeline"]


def get_project(component: str) -> dict[str, Any]:
    group_data = _do_get_dict(path=f"/groups/{TOOLFORGE_GROUP_ID}")
    for repo in group_data["projects"]:
        if repo["path"] == component:
            return repo

    component_list = [repo["path"] for repo in group_data["projects"]]
    raise Exception(f"Unable to find component {component} in toolforge, found: {component_list}")


def get_branch_mr(project: dict[str, Any], branch: str) -> int:
    all_mrs = get_mrs(project=project)
    for mr in all_mrs:
        if mr["source_branch"] == branch:
            return int(mr["iid"])

    raise Exception(f"No merge requests found for branch {branch} for project {project['name']}")


def get_artifacts_url(component: str, branch: str) -> str:
    project = get_project(component=component)
    mr_number = get_branch_mr(project=project, branch=branch)
    pipeline = get_last_pipeline(project=project, mr_number=mr_number)
    package_job = get_package_job(project=project, pipeline=pipeline)
    return f"{GITLAB_API_BASE_URL}/projects/{project['id']}/jobs/{package_job['id']}/artifacts"


class GitlabController:

    def __init__(self, private_token: str | None = None):
        # this combo is needed as sometimes it decides that Gitlab is not a member (so no-member), but sometimes it
        # decides it is (so useless-suppression)
        # pylint: disable=useless-suppression
        # pylint: disable=no-member
        self.gitlab = upstream_gitlab_lib.Gitlab(url=GITLAB_BASE_URL, private_token=private_token)

    def get_project_id_by_name(self, project_name: str) -> int:
        projects = self.gitlab.projects.list(all=True, search=project_name)
        for project in projects:
            if project.name == project_name:
                return project.id

        raise Exception(f"could not find project '{project_name}'")

    def create_mr_note(self, project_id: int, merge_request_iid: int, note_body: str) -> Any:
        project = self.gitlab.projects.get(project_id)
        mr = project.mergerequests.get(merge_request_iid)
        note = mr.notes.create({"body": note_body})
        return note
