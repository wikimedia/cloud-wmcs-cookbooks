"""WMCS Toolforge - Copy an Apt package from the toolsbeta repo to the main tools repo

Usage example:
    cookbook wmcs.toolforge.apt.copy_to_main_repo \
        --package toolforge-jobs-framework-cli \
        --version 12
"""

from __future__ import annotations

import argparse
import logging
from typing import Optional

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.aptly import Aptly
from wmcs_libs.common import (
    CommonOpts,
    WMCSCookbookRunnerBase,
    add_common_opts,
    parser_type_str_hostname,
    with_common_opts,
)

LOGGER = logging.getLogger(__name__)


class ToolforgeCopyAptPackageToMainRepo(CookbookBase):
    """Toolforge cookbook to copy an Apt package to the main repo"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        add_common_opts(parser, project_default="tools")
        parser.add_argument(
            "--aptly-hostname",
            required=False,
            type=parser_type_str_hostname,
            help="The hostname of the Aptly node. Default is '<project>-services-05'",
        )
        parser.add_argument(
            "--package",
            required=True,
            help="Package to operate on",
        )
        parser.add_argument(
            "--version",
            required=True,
            help="Specific package version to operate on",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            ToolforgeCopyAptPackageToMainRepoRunner,
        )(
            aptly_hostname=args.aptly_hostname,
            package=args.package,
            version=args.version,
            spicerack=self.spicerack,
        )


class ToolforgeCopyAptPackageToMainRepoRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeCopyAptPackageToMainRepo"""

    def __init__(
        self,
        common_opts: CommonOpts,
        aptly_hostname: Optional[str],
        package: str,
        version: str,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.aptly_hostname = aptly_hostname or f"{self.common_opts.project}-services-05"
        self.package = package
        self.version = version

        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for package '{self.package}' version '{self.version}'"

    def run(self) -> None:
        """Main entry point"""
        aptly_fqdn = f"{self.aptly_hostname}.{self.common_opts.project}.eqiad1.wikimedia.cloud"
        LOGGER.info("INFO: using Aptly node FQDN %s", aptly_fqdn)
        aptly = Aptly(self.spicerack.remote().query(f"D{{{aptly_fqdn}}}", use_sudo=True))

        distros = []

        for repository in aptly.get_repositories():
            if not repository.endswith("-toolsbeta"):
                continue

            packages = aptly.get_packages_in_repository(repository, self.package, self.version)

            if not packages:
                continue

            distros.append(repository.replace("-toolsbeta", ""))

            target_repository = repository.replace("-toolsbeta", "-tools")
            for package in packages:
                LOGGER.info("Copying %s from %s to %s", package, repository, target_repository)
                aptly.copy(package, repository, target_repository)
            aptly.publish(target_repository)
