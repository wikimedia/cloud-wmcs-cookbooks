r"""Add a new etcd node to an existing cluster.

Usage example:
    cookbook wmcs.etcd.add_cluster_node \
        --cluster-name toolsbeta-k8s

"""

# pylint: disable=too-many-arguments
from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.etcd.lib.add_node_to_cluster import AddNodeToCluster
from cookbooks.wmcs.vps.create_instance_with_prefix import CreateInstanceWithPrefix
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase
from wmcs_libs.etcd.clusters import (
    add_etcd_cluster_opts,
    get_cluster_node_prefix,
    get_cluster_node_server_group_name,
    get_cluster_security_group_name,
    with_etcd_cluster_opts,
)
from wmcs_libs.inventory.etcd import EtcdClusterName

LOGGER = logging.getLogger(__name__)


class ToolforgeAddK8sEtcdNode(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):
        parser = super().argument_parser()
        add_etcd_cluster_opts(parser)
        parser.add_argument(
            "--skip-puppet-bootstrap",
            action="store_true",
            help=(
                "Skip all the puppet bootstrapping section, useful if you already did it and you are rerunning, or if "
                "you did it manually."
            ),
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

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        return with_etcd_cluster_opts(
            self.spicerack,
            args,
            ToolforgeAddK8sEtcdNodeRunner,
        )(
            skip_puppet_bootstrap=args.skip_puppet_bootstrap,
            image=args.image,
            flavor=args.flavor,
            spicerack=self.spicerack,
        )


class ToolforgeAddK8sEtcdNodeRunner(WMCSCookbookRunnerBase):
    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: EtcdClusterName,
        spicerack: Spicerack,
        skip_puppet_bootstrap: bool,
        image: str | None = None,
        flavor: str | None = None,
    ):

        self.common_opts = common_opts
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.skip_puppet_bootstrap = skip_puppet_bootstrap
        self.image = image
        self.flavor = flavor

    def run(self) -> None:
        etcd_prefix = get_cluster_node_prefix(self.cluster_name)

        security_group = get_cluster_security_group_name(self.cluster_name)
        server_group = get_cluster_node_server_group_name(self.cluster_name)

        start_args = [
            "--project",
            self.common_opts.project,
            "--prefix",
            etcd_prefix,
            "--security-group",
            security_group,
            "--server-group",
            server_group,
        ]
        if self.image:
            start_args.extend(["--image", self.image])

        if self.flavor:
            start_args.extend(["--flavor", self.flavor])

        create_instance_cookbook = CreateInstanceWithPrefix(spicerack=self.spicerack)
        new_member = create_instance_cookbook.get_runner(
            args=create_instance_cookbook.argument_parser().parse_args(start_args)
        ).create_instance()

        add_node_to_cluster_args = [
            "--cluster-name",
            self.cluster_name.value,
            "--new-member-fqdn",
            new_member.server_fqdn,
        ]
        if self.skip_puppet_bootstrap:
            add_node_to_cluster_args.append("--skip-puppet-bootstrap")
        add_node_to_cluster_cookbook = AddNodeToCluster(spicerack=self.spicerack)
        add_node_to_cluster_cookbook.get_runner(
            args=add_node_to_cluster_cookbook.argument_parser().parse_args(add_node_to_cluster_args),
        ).run()

        LOGGER.info("Added a new node %s to etcd cluster", new_member.server_fqdn)
