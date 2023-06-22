"""Aptly related classes and functions."""
from __future__ import annotations

from spicerack.remote import RemoteExecutionError

from wmcs_libs.common import CommandRunnerMixin


class Aptly(CommandRunnerMixin):
    """Class to manage an Aptly-based apt repository."""

    def _get_full_command(self, *command: str, json_output: bool = True, project_as_arg: bool = False):
        return command

    def get_repositories(self) -> list[str]:
        """List all repositories in this Aptly install."""
        return self.run_raw("aptly", "repo", "list", "-raw").strip().split("\n")

    def get_packages_in_repository(self, repository: str, package: str, version: str | None) -> list[str]:
        """Lists all copies of a package (and maybe a version) in a specific repository.

        In our install there's in practice going to be only one copy per version,
        but in theory there could be builds for multiple architectures.
        """
        # https://www.aptly.info/doc/feature/query/
        version_query = f" (= {version})" if version else ""
        try:
            return (
                self.run_raw("aptly", "repo", "search", repository, f"'{package}{version_query}'").strip().split("\n")
            )
        except RemoteExecutionError as e:
            if e.retcode == 2:
                # Aptly throws an error when a package was not found.
                return []
            raise

    def copy(self, package: str, source_repository: str, target_repository: str):
        """Copies a package from one repository to another."""
        self.run_raw("aptly", "repo", "copy", source_repository, target_repository, package)

    def publish(self, repository: str):
        """Publishes updates to a repository."""
        self.run_raw("aptly", "publish", "update", "--skip-signing", repository)
