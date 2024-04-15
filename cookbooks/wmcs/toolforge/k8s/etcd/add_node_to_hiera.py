r"""WMCS Toolforge - Add a new etcd node to hiera

Usage examples:
    cookbook wmcs.toolforge.k8s.etcd.add_node_to_hiera \
        --cluster-name toolsbeta \
        --fqdn-to-add toolsbeta-k8s-etcd-09.toolsbeta.eqiad1.wikimedia.cloud

"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

import yaml
from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, CuminParams, OutputFormat, WMCSCookbookRunnerBase, run_one_as_dict
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_cluster_node_prefix,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.openstack.common import get_control_nodes

LOGGER = logging.getLogger(__name__)


class AddNodeToHiera(CookbookBase):
    """WMCS Toolforge cookbook to add a new etcd node to hiera"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_toolforge_kubernetes_cluster_opts(parser)
        parser.add_argument("--fqdn-to-add", required=True, help="FQDN of the node to add")

        return parser

    def get_runner(self, args: argparse.Namespace) -> "AddNodeToHieraRunner":
        """Get Runner"""
        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            AddNodeToHieraRunner,
        )(
            fqdn_to_add=args.fqdn_to_add,
            spicerack=self.spicerack,
        )


class AddNodeToHieraRunner(WMCSCookbookRunnerBase):
    """Runner for AddNodeToHiera"""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        spicerack: Spicerack,
        fqdn_to_add: str,
    ):
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.fqdn_to_add = fqdn_to_add

    def run(self) -> None:
        """Main entry point"""
        self.add_node_to_hiera()

    def add_node_to_hiera(self) -> dict[str, Any]:
        """Needed to be able to change the return type."""
        openstack_control_node_fqdn = get_control_nodes(self.cluster_name.get_openstack_cluster_name())[1]
        control_node = self.spicerack.remote().query(f"D{{{openstack_control_node_fqdn}}}", use_sudo=True)

        etcd_prefix = get_cluster_node_prefix(self.cluster_name, ToolforgeKubernetesNodeRoleName.ETCD)

        response = run_one_as_dict(
            node=control_node,
            command=["wmcs-enc-cli", "--openstack-project", self.common_opts.project, "get_prefix_hiera", etcd_prefix],
            cumin_params=CuminParams(is_safe=True),
            try_format=OutputFormat.YAML,
        )
        # double yaml yep xd
        current_hiera_config = yaml.safe_load(response["hiera"])
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
            # json is a one-line string, with only double quotes, nicer for
            # usage as cli parameter, and it's valid yaml :)
            current_hiera_config_str = json.dumps(current_hiera_config)
            LOGGER.info("New hiera config:\n%s", current_hiera_config_str)

            run_one_as_dict(
                node=control_node,
                command=(
                    "wmcs-enc-cli",
                    "--openstack-project",
                    self.common_opts.project,
                    "set_prefix_hiera",
                    etcd_prefix,
                    f"'{current_hiera_config_str}'",
                ),
                try_format=OutputFormat.YAML,
            )
        else:
            LOGGER.info("Hiera config was already correct.")

        return current_hiera_config
