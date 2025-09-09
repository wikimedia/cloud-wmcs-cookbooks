r"""WMCS VPS - Add a user to a project.

Usage example:
    cookbook wmcs.vps.add_user_to_project \
        --cluster-name eqiad1 \
        --project toolsbeta \
        --user dcaro \
        --as-member

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class AddUserToProject(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self) -> argparse.ArgumentParser:

        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=False,
            choices=list(OpenstackClusterName),
            default=OpenstackClusterName.EQIAD1,
            type=OpenstackClusterName,
            help="Openstack cluster name to use.",
        )
        parser.add_argument(
            "--user",
            help="Username to add to the project",
            required=True,
        )
        parser.add_argument(
            "--as-member",
            action="store_true",
            default=False,
            help="If set, the user will be added as project admin (otherwise will just add as reader)",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(
            self.spicerack,
            args,
            AddUserToProjectRunner,
        )(
            user=args.user,
            cluster_name=args.cluster_name,
            as_member=args.as_member,
            spicerack=self.spicerack,
        )


class AddUserToProjectRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        user: str,
        as_member: bool,
        cluster_name: OpenstackClusterName,
        spicerack: Spicerack,
    ):

        self.common_opts = common_opts
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=cluster_name,
            project=self.common_opts.project,
        )

        self.user = user
        if " " in self.user:
            message = ""
            message += "No spaces allowed in the user name. You likely need to "
            message += "translate the Wiki account name to the Unix account name via "
            message += "`user@cloudcontrol1011:~$ sudo wmcs-openstack user list | grep -i username`"
            raise ValueError(message)

        self.role_name = "member" if as_member else "reader"
        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for user '{self.user}' in role '{self.role_name}'"

    def run(self) -> None:

        self.openstack_api.role_add(role_name=self.role_name, user_name=self.user)

        if self.role_name == "member":
            # if we leave only the member role, users wont be able to SSH. Add 'reader' too.
            self.openstack_api.role_add(role_name="reader", user_name=self.user)
