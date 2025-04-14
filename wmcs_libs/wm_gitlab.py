#!/usr/bin/env python3
from __future__ import annotations

import base64
import time
from logging import getLogger
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
LOGGER = getLogger(__name__)


class GitlabError(Exception):
    pass


class MrNotFound(GitlabError):
    pass


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

    raise MrNotFound(f"No merge requests found for branch {branch} for project {project['name']}")


class GitlabController:
    def __init__(self, private_token: str | None = None):
        # this combo is needed as sometimes it decides that Gitlab is not a member (so no-member), but sometimes it
        # decides it is (so useless-suppression)
        # pylint: disable=useless-suppression
        # pylint: disable=no-member
        # ssl_verify false needed to run on laptops as they don't have the CA installed
        self.gitlab = upstream_gitlab_lib.Gitlab(
            url=GITLAB_BASE_URL,
            private_token=private_token,
            ssl_verify=False,
        )

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

    def get_artifact_job_id_from_branch(self, branch: str, component: str) -> str:
        all_projects_with_name = list(self.gitlab.projects.list(all=True, search=component))
        maybe_projects = [
            project for project in all_projects_with_name if project.namespace["id"] == TOOLFORGE_GROUP_ID
        ]
        if not maybe_projects:
            raise Exception(
                f"Unable to find project for component {component} under the toolforge group (id={TOOLFORGE_GROUP_ID})"
            )

        project = maybe_projects[0]

        maybe_jobs = [
            job
            for job in project.jobs.list(get_all=False, query_params={"scope[]": ["success", "pending"]})
            if job.name == PACKAGE_JOB_NAME and job.ref == branch
        ]
        if not maybe_jobs:
            raise Exception(f"Unable to find package build job for component {component}, branch {branch}")

        job = maybe_jobs[0]
        while job.status in ["running", "pending"]:
            print(f"Job {job.id} is still {job.status}, waiting for it to finish....")
            time.sleep(10)
            maybe_jobs = [
                job
                for job in project.jobs.list(get_all=False, query_params={"scope[]": ["success", "pending"]})
                if job.name == PACKAGE_JOB_NAME and job.ref == branch
            ]
            if not maybe_jobs:
                raise Exception(f"Unable to find project for component {component}")
            job = maybe_jobs[0]

        return job.id

    def get_artifacts_url(self, component: str, branch: str) -> str:
        project = get_project(component=component)
        try:
            mr_number = get_branch_mr(project=project, branch=branch)
            LOGGER.info("Found mr %d for branch %s", mr_number, branch)
            pipeline = get_last_pipeline(project=project, mr_number=mr_number)
            package_job_id = get_package_job(project=project, pipeline=pipeline)["id"]
        except MrNotFound:
            LOGGER.info("No mr found for branch %s, using latest branch package job", branch)
            # we try to get it from the branch directly, instead of an open MR
            package_job_id = self.get_artifact_job_id_from_branch(branch=branch, component=component)

        return f"{GITLAB_API_BASE_URL}/projects/{project['id']}/jobs/{package_job_id}/artifacts"

    def get_file_at_commit(self, project: str, file_path: str, commit_sha: str) -> str:
        project_id = self.get_project_id_by_name(project_name=project)
        project_obj = self.gitlab.projects.get(id=project_id)
        return base64.b64decode(project_obj.files.get(file_path=file_path, ref=commit_sha).content).decode("utf8")

    def create_commit(  # pylint: disable=too-many-arguments
        self,
        project: str,
        new_branch: str,
        actions: list[dict[str, Any]],
        commit_message: str,
        author_email: str,
        author_name: str,
        base_branch: str = "main",
    ) -> dict[str, Any]:
        """Creates a new branch with the a new commit with all the given actions applied.

        To see the shape of actions see:
        https://docs.gitlab.com/api/commits/#create-a-commit-with-multiple-files-and-actions
        """
        project_id = self.get_project_id_by_name(project_name=project)
        project_obj = self.gitlab.projects.get(id=project_id)
        return project_obj.commits.create(
            data={
                "actions": actions,
                "commit_message": commit_message,
                "start_branch": base_branch,
                "branch": new_branch,
                "author_email": author_email,
                "author_name": author_name,
            },
        )

    def create_mr(
        self, project: str, source_branch: str, title: str, target_branch: str = "main"
    ) -> upstream_gitlab_lib.v4.objects.ProjectMergeRequest:
        project_id = self.get_project_id_by_name(project_name=project)
        project_obj = self.gitlab.projects.get(id=project_id)
        new_mr = project_obj.mergerequests.create(
            data={
                "title": title,
                "source_branch": source_branch,
                "target_branch": target_branch,
                "remove_source_branch": True,
            },
        )
        # needed as the api return a proxy and the types are not properly declared
        return cast(upstream_gitlab_lib.v4.objects.ProjectMergeRequest, new_mr)

    def get_mr(
        self,
        project: str,
        mr_iid: str,
    ) -> upstream_gitlab_lib.v4.objects.ProjectMergeRequest:
        project_id = self.get_project_id_by_name(project_name=project)
        project_obj = self.gitlab.projects.get(id=project_id)
        mr = project_obj.mergerequests.get(id=mr_iid)
        return cast(upstream_gitlab_lib.v4.objects.ProjectMergeRequest, mr)
