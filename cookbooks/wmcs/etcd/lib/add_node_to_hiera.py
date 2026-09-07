r"""Add a new etcd node to Hiera for a given cluster.

Usage examples:
    cookbook wmcs.etcd.lib.add_node_to_hiera \
        --cluster-name toolsbeta-k8s \
        --fqdn-to-add toolsbeta-test-k8s-etcd-9.toolsbeta.eqiad1.wikimedia.cloud

"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase
from wmcs_libs.etcd.clusters import (
    add_etcd_cluster_opts,
    get_cluster_node_prefix,
    with_etcd_cluster_opts,
)
from wmcs_libs.inventory.etcd import EtcdClusterName
from wmcs_libs.openstack.enc import Enc

LOGGER = logging.getLogger(__name__)


class AddNodeToHiera(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):
        parser = super().argument_parser()
        add_etcd_cluster_opts(parser)
        parser.add_argument("--fqdn-to-add", required=True, help="FQDN of the node to add")

        return parser

    def get_runner(self, args: argparse.Namespace) -> "AddNodeToHieraRunner":
        """Get Runner"""
        return with_etcd_cluster_opts(
            self.spicerack,
            args,
            AddNodeToHieraRunner,
        )(
            fqdn_to_add=args.fqdn_to_add,
            spicerack=self.spicerack,
        )


class AddNodeToHieraRunner(WMCSCookbookRunnerBase):
    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: EtcdClusterName,
        spicerack: Spicerack,
        fqdn_to_add: str,
    ):

        self.common_opts = common_opts
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.fqdn_to_add = fqdn_to_add

    def run(self) -> None:
        self.add_node_to_hiera()

    def add_node_to_hiera(self) -> dict[str, Any]:
        """Needed to be able to change the return type."""
        enc = Enc(remote=self.spicerack.remote(), cluster_name=self.cluster_name.get_openstack_cluster_name())
        enc_prefix = enc.prefix(self.cluster_name.get_project(), get_cluster_node_prefix(self.cluster_name))

        current_hiera_config = enc_prefix.get_current_hiera()
        changed = False

        nodes = current_hiera_config.get("profile::toolforge::k8s::etcd_nodes", [])
        if self.fqdn_to_add not in nodes:
            nodes.append(self.fqdn_to_add)
            changed = True

        current_hiera_config["profile::toolforge::k8s::etcd_nodes"] = nodes

        alt_names = current_hiera_config.get("profile::puppet::agent::dns_alt_names", [])
        if self.fqdn_to_add not in alt_names:
            alt_names.append(self.fqdn_to_add)
            changed = True

        current_hiera_config["profile::puppet::agent::dns_alt_names"] = alt_names

        if changed:
            enc_prefix.replace_hiera(current_hiera_config)
        else:
            LOGGER.info("Hiera config was already correct.")

        return current_hiera_config
