r"""WMCS Toolforge - Add a new instance to the given prefix.

It will make sure to use the same flavor, network, groups and increment the
index of the existing instance with the same prefix unless you pass a specific
one.

Usage example:
    cookbook wmcs.vps.create_instance_with_prefix \
        --project toolsbeta \
        --prefix toolsbeta-k8s-test-etcd \
        --security-group toolsbeta-k8s-full-connectivity

"""

# pylint: disable=too-many-arguments,no-value-for-parameter
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Callable

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from spicerack.remote import RemoteExecutionError
from wmflib.decorators import retry

from cookbooks.wmcs.vps.refresh_puppet_certs import RefreshPuppetCerts
from wmcs_libs.common import (
    CommonOpts,
    CuminParams,
    WMCSCookbookRunnerBase,
    add_common_opts,
    natural_sort_key,
    run_one_raw,
    with_common_opts,
)
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import OpenstackAPI, OpenstackIdentifier, OpenstackServerGroupPolicy

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateServerResponse:
    """Instance creation results."""

    server_id: OpenstackIdentifier
    server_fqdn: str


@dataclass(frozen=True)
class InstanceCreationOpts:
    """Instance creation options."""

    prefix: str | None = None
    flavor: OpenstackIdentifier | None = None
    image: OpenstackIdentifier | None = None
    network: OpenstackIdentifier | None = None
    security_group: OpenstackIdentifier | None = None
    server_group: OpenstackIdentifier | None = None
    server_group_policy: OpenstackServerGroupPolicy | None = None

    def to_cli_args(self) -> list[str]:
        """Helper to unwrap the options for use with argument parsers."""
        args = []
        if self.prefix:
            args.extend(["--prefix", self.prefix])
        if self.flavor:
            args.extend(["--flavor", self.flavor])
        if self.image:
            args.extend(["--image", self.image])
        if self.network:
            args.extend(["--network", self.network])
        if self.security_group:
            args.extend(["--security-group", self.security_group])
        if self.server_group:
            args.extend(["--server-group", self.server_group])
        if self.server_group_policy:
            args.extend(["--server-group-policy", self.server_group_policy.value])

        return args


def add_instance_creation_options(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Adds the common instance creation option to a parser."""
    parser.add_argument(
        "--prefix",
        required=False,
        default=None,
        help="Prefix for the instance (ex. toolsbeta-test-k8s-etcd).",
    )
    parser.add_argument(
        "--flavor",
        required=False,
        default=None,
        help=(
            "Flavor for the new instance (will use the same as the latest existing one by default, ex. "
            "g2.cores4.ram8.disk80, ex. 06c3e0a1-f684-4a0c-8f00-551b59a518c8)."
        ),
    )
    parser.add_argument(
        "--image",
        required=False,
        default=None,
        help=(
            "Image for the new instance (will use the same as the latest existing one by default, ex. "
            "debian-10.0-buster, ex. 64351116-a53e-4a62-8866-5f0058d89c2b)"
        ),
    )
    parser.add_argument(
        "--network",
        required=False,
        default=None,
        help=(
            "Network for the new instance (will use the same as the latest existing one by default, ex. "
            "lan-flat-cloudinstances2b, ex. a69bdfad-d7d2-4cfa-8231-3d6d3e0074c9)"
        ),
    )
    parser.add_argument(
        "--security-group",
        required=False,
        default=None,
        help=(
            "Extra security group to put the instance in, in addition to the 'default' group. "
            "The given security group will be created if it does not exist."
        ),
    )
    parser.add_argument(
        "--server-group",
        required=False,
        help=(
            "Server group to start the instance in. If it does not exist, it will create it with the given "
            "server-group-policy, will use the same as '--prefix' by default (ex. toolsbeta-test-k8s-etcd)."
        ),
    )
    parser.add_argument(
        "--server-group-policy",
        required=False,
        help=(
            "Server group policy to start the instance in. If it does not exist, it will create it with "
            "anti-affinity policy."
        ),
        choices=list(OpenstackServerGroupPolicy),
        type=OpenstackServerGroupPolicy,
        default=OpenstackServerGroupPolicy.ANTI_AFFINITY,
    )
    return parser


def with_instance_creation_options(args: argparse.Namespace, runner: Callable) -> Callable:
    """Wraps a CookbookRunnerBase to pass to it the instance creation options.

    This allows to fully encapsulate the instance creation options and avoid the need to change anything in the code
    that uses them (ex. if you add a new option to the creation options).

    Example:
    >> class MyCookbook(CookbookBase):
    >>     def argument_parser(self) -> argparse.ArgumentParser:
    >>         my_parser = add_instance_creation_options(ArgumentParser(...))
    >>         # Add your options/arguments
    >>         my_parser.add_argument("--my-option1", default=None)
    >>         return my_parser
    >>
    >>     def get_runner(self, args: argparse.Namespace) -> CookbookRunnerBaseWithProxy:
    >>         return with_instance_creation_options(
    >>             args=args, runner=MyCookbookRunner
    >>         )(my_option1=args.my_option1, spicerack=self.spicerack)
    >>

    For a full Cookbook example, see cookbooks.wmcs.vps.create_instance_with_prefix.CreateInstanceWithPrefix.

    """
    instance_creation_opts = InstanceCreationOpts(
        prefix=args.prefix,
        flavor=args.flavor,
        image=args.image,
        network=args.network,
        security_group=args.security_group,
        server_group=args.server_group,
        server_group_policy=args.server_group_policy,
    )
    return partial(runner, instance_creation_opts=instance_creation_opts)


class CreateInstanceWithPrefix(CookbookBase):
    """WMCS cookbook to create and start a new instance following a prefix."""

    title = __doc__

    def argument_parser(self) -> argparse.ArgumentParser:
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        add_instance_creation_options(parser)
        parser.add_argument(
            "--ssh-retries",
            required=False,
            default=15,
            type=int,
            help=(
                "Number of time that it will try to ssh to the new instance after starting it up, it will wait for "
                "1min between tries."
            ),
        )
        parser.add_argument(
            "--sign-puppet-certs",
            action="store_true",
            help="Enroll the newly created instance on the project Puppet server",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> "CreateInstanceWithPrefixRunner":
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            with_instance_creation_options(
                args,
                runner=CreateInstanceWithPrefixRunner,
            ),
        )(
            security_group=args.security_group,
            server_group=args.server_group,
            server_group_policy=args.server_group_policy,
            ssh_retries=args.ssh_retries,
            sign_puppet_certs=args.sign_puppet_certs,
            spicerack=self.spicerack,
        )


class CreateInstanceWithPrefixRunner(WMCSCookbookRunnerBase):
    """Runner for CreateInstanceWithPrefix"""

    def __init__(
        self,
        common_opts: CommonOpts,
        spicerack: Spicerack,
        instance_creation_opts: InstanceCreationOpts,
        server_group_policy: OpenstackServerGroupPolicy,
        security_group: str,
        server_group: str | None = None,
        sign_puppet_certs: bool = False,
        ssh_retries: int = 15,
    ):
        """Init"""
        self.common_opts = common_opts
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(), cluster_name=OpenstackClusterName.EQIAD1, project=self.common_opts.project
        )
        if instance_creation_opts.prefix is None:
            raise Exception("Instance prefix missing, please pass one")

        self.prefix = instance_creation_opts.prefix
        self.flavor = instance_creation_opts.flavor
        self.network = instance_creation_opts.network
        self.image = instance_creation_opts.image
        self.server_group = server_group if server_group is not None else self.prefix
        self.server_group_policy = server_group_policy
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.security_group = security_group
        self.ssh_retries = ssh_retries
        self.sign_puppet_certs = sign_puppet_certs

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"with prefix '{self.prefix}'"

    def run(self) -> None:
        """Main entry point"""
        self.create_instance()

    def _get_security_group_id(self, name: str) -> str:
        maybe_security_group = self.openstack_api.security_group_by_name(
            name=name, cumin_params=CuminParams(print_output=False)
        )
        if maybe_security_group is None:
            raise Exception(
                f"Unable to find a '{name}' security group for project {self.common_opts.project}, "
                "though it should have been created before if not there."
            )

        return maybe_security_group["ID"]

    def create_instance(self) -> CreateServerResponse:  # pylint: disable=too-many-locals
        """We need this as `run` is an inherited function with a return type we should not override."""
        if self.security_group:
            self.openstack_api.security_group_ensure(
                security_group=self.security_group,
            )

        self.openstack_api.server_group_ensure(
            server_group=self.server_group,
            policy=self.server_group_policy,
        )
        no_output = CuminParams(print_output=False)

        all_project_servers = self.openstack_api.server_list(cumin_params=no_output)
        other_prefix_members = list(
            sorted(
                (
                    server
                    for server in all_project_servers
                    if server.get("Name", "noname").startswith(f"{self.prefix}-")
                ),
                key=lambda server: natural_sort_key(server.get("Name", "noname-0")),
            )
        )
        if not other_prefix_members:
            missing_params = [
                param_name for param_name in ["flavor", "image", "network"] if not getattr(self, param_name)
            ]
            if missing_params:
                message = (
                    f"As there's no other prefix members (prefix={self.prefix}), I can't add a new member without "
                    f"explicitly specifying the missing {', '.join(missing_params)} options."
                )
                LOGGER.error(message)
                raise Exception(message)

            last_prefix_member_id = 0

        else:
            # the trimming by length of the prefix allows prefixes with trailing integers (ex. tools-sgeexec-09)
            # so 1 will be extracted as id, instead of 901 for tools-sgeexec-0901
            last_prefix_member_id = max(
                int(member["Name"][len(self.prefix) :].rsplit("-", 1)[-1]) for member in other_prefix_members
            )

        new_prefix_member_name = f"{self.prefix}-{last_prefix_member_id + 1}"

        security_groups = [self._get_security_group_id("default")]
        if self.security_group:
            security_groups.append(self._get_security_group_id(self.security_group))

        maybe_server_group = self.openstack_api.server_group_by_name(name=self.server_group, cumin_params=no_output)
        if maybe_server_group is None:
            raise Exception(
                f"Unable to find a server group with name {self.server_group} for project {self.common_opts.project}, "
                "though it should have been created before if not there."
            )

        server_group_id: str = maybe_server_group["ID"]

        new_instance_id = self.openstack_api.server_create(
            flavor=self.flavor or other_prefix_members[-1]["Flavor"],
            security_group_ids=security_groups,
            server_group_id=server_group_id,
            image=self.image or other_prefix_members[-1]["Image"],
            network=self.network or list(other_prefix_members[-1]["Networks"].keys())[0],
            name=new_prefix_member_name,
        )

        new_instance_fqdn = f"{new_prefix_member_name}.{self.common_opts.project}.eqiad1.wikimedia.cloud"
        new_prefix_node = self.spicerack.remote().query(f"D{{{new_instance_fqdn}}}", use_sudo=True)

        @retry(
            tries=self.ssh_retries,
            delay=timedelta(minutes=1),
            backoff_mode="constant",
            exceptions=(RemoteExecutionError,),
        )
        def try_to_reach_the_new_instance():
            return run_one_raw(node=new_prefix_node, command=["cat", "/.cloud-init-finished"]).strip()

        result = try_to_reach_the_new_instance()

        if "mesg: ttyname failed" in result:
            # Ugly workaround for https://gerrit.wikimedia.org/r/c/operations/software/spicerack/+/730270
            run_one_raw(
                node=new_prefix_node,
                command=["sed", "-i", "-e", "'s/mesg n || true/mesg n 2>/dev/null || true/'", "/root/.profile"],
            )

        if self.sign_puppet_certs:
            refresh_puppet_certs_cookbook = RefreshPuppetCerts(spicerack=self.spicerack)
            refresh_puppet_certs_cookbook.get_runner(
                args=refresh_puppet_certs_cookbook.argument_parser().parse_args(["--fqdn", new_instance_fqdn]),
            ).run()

        return CreateServerResponse(server_id=new_instance_id, server_fqdn=new_instance_fqdn)
