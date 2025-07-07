"""Aptly related classes and functions."""

from __future__ import annotations

from pathlib import Path

from wmcs_libs.common import CUMIN_SAFE_WITH_OUTPUT, CUMIN_UNSAFE_WITHOUT_OUTPUT, CommandRunnerMixin

SUPPORTED_DISTROS = ["buster", "bullseye", "bookworm"]


class Aptly(CommandRunnerMixin):
    """Class to manage an Aptly-based apt repository."""

    def _get_full_command(
        self, *command: str, json_output: bool = True, project_as_arg: bool = False, with_env_var: bool = True
    ):
        return command

    def get_repositories(self) -> list[str]:
        """List all repositories in this Aptly install."""
        return self.run_raw("aptly", "repo", "list", "-raw", cumin_params=CUMIN_SAFE_WITH_OUTPUT).strip().split("\n")

    def add(self, package_path: Path, repository: str):
        """Copies a package from one repository to another."""
        self.run_raw("aptly", "repo", "add", repository, str(package_path), cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT)

    def publish(self, repository: str):
        """Publishes updates to a repository."""
        self.run_raw(
            "aptly",
            "-architectures=arm64,amd64,all",
            "publish",
            "update",
            "--skip-signing",
            repository,
            cumin_params=CUMIN_UNSAFE_WITHOUT_OUTPUT,
        )
