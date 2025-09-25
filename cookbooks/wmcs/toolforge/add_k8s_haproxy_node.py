r"""WMCS Toolforge - Add a new HAProxy node to Toolforge Kubernetes clsuter.

Usage example:
    cookbook wmcs.toolforge.add_k8s_haproxy_node \
        --cluster-name toolsbeta

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from cookbooks.wmcs.vps.create_instance_with_prefix import CreateInstanceWithPrefix, CreateServerResponse
from wmcs_libs.common import (
    CommonOpts,
    WMCSCookbookRunnerBase,
    get_ip_address_family,
)
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_cluster_node_prefix,
    get_cluster_node_server_group_name,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubeadm import (
    HAPROXY_KEEPALIVED_PEERS_HIERA_KEY,
    HAPROXY_KEEPALIVED_VIPS_HIERA_KEY,
)
from wmcs_libs.openstack.common import OpenstackAPI
from wmcs_libs.openstack.enc import Enc

LOGGER = logging.getLogger(__name__)


class ToolforgeAddK8sHaproxyNode(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_toolforge_kubernetes_cluster_opts(parser)
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
                "VLAN/legacy, ex. a69bdfad-d7d2-4cfa-8231-3d6d3e0074c9)"
            ),
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeAddK8sHaproxyNodeRunner,
        )(
            flavor=args.flavor,
            image=args.image,
            network=args.network,
            spicerack=self.spicerack,
        )


class ToolforgeAddK8sHaproxyNodeRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        spicerack: Spicerack,
        flavor: str | None = None,
        image: str | None = None,
        network: str | None = None,
    ):  # pylint: disable=too-many-arguments

        self.common_opts = common_opts
        self.cluster_name = cluster_name

        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=self.cluster_name.get_openstack_cluster_name(),
            project=self.cluster_name.get_project(),
        )

        self.enc = Enc(remote=self.spicerack.remote(), cluster_name=self.cluster_name.get_openstack_cluster_name())

        self.image = image
        self.flavor = flavor
        self.network = network

    def _attach_service_vip(self, new_server: CreateServerResponse):
        config = self.enc.node_config(self.cluster_name.get_project(), new_server.server_hostname)
        vips = config.hiera.get(HAPROXY_KEEPALIVED_VIPS_HIERA_KEY, [])
        if not vips:
            raise ValueError("No VIPs found?")

        ips = []
        dns = self.spicerack.dns()
        for vip in vips:
            if get_ip_address_family(vip) is not None:
                ips.append(vip)
            else:
                ips.extend(dns.resolve_ips(vip))

        server_port = self.openstack_api.port_get_for_server(new_server.server_id)[0]
        self.openstack_api.attach_service_ips(vips, server_port.port_id)

    def _update_enc_node_list(self, new_server: CreateServerResponse, hiera_prefix: str) -> list[str]:
        enc_prefix = self.enc.prefix(
            self.cluster_name.get_project(),
            hiera_prefix,
        )

        current_hiera = enc_prefix.get_current_hiera()

        # save a copy to return later
        current_nodes = current_hiera.get(HAPROXY_KEEPALIVED_PEERS_HIERA_KEY, [])

        current_hiera[HAPROXY_KEEPALIVED_PEERS_HIERA_KEY] = [*current_nodes, new_server.server_fqdn]
        enc_prefix.set_hiera_values(current_hiera)

        return current_nodes

    def run(self) -> None:

        haproxy_prefix = get_cluster_node_prefix(self.cluster_name, ToolforgeKubernetesNodeRoleName.HAPROXY)
        server_group = get_cluster_node_server_group_name(self.cluster_name, ToolforgeKubernetesNodeRoleName.HAPROXY)

        start_args = [
            "--project",
            self.common_opts.project,
            "--prefix",
            haproxy_prefix,
            "--security-group",
            # This is intentionally haproxy_prefix. We don't want to re-use the
            # full-connectivity one since we don't need that, and haproxy_prefix
            # is already prefixed and is role-specific.
            haproxy_prefix,
            "--server-group",
            server_group,
            "--sign-puppet-certs",
        ]
        if self.image:
            start_args.extend(["--image", self.image])
        if self.flavor:
            start_args.extend(["--flavor", self.flavor])
        if self.network:
            start_args.extend(["--network", self.network])

        create_instance_cookbook = CreateInstanceWithPrefix(spicerack=self.spicerack)
        new_member = create_instance_cookbook.get_runner(
            args=create_instance_cookbook.argument_parser().parse_args(start_args)
        ).create_instance()
        remote = self.spicerack.remote().query(f"D{{{new_member.server_fqdn}}}", use_sudo=True)

        LOGGER.info("Disabling Puppet so that Keepalived does not start before it should")
        puppet = self.spicerack.puppet(remote)
        puppet_reason = self.spicerack.admin_reason("host is in setup")
        puppet.disable(puppet_reason)

        LOGGER.info("Attaching service VIP to the new server")
        self._attach_service_vip(new_member)

        LOGGER.info("Adding new server to the list of peers in Hiera")
        other_nodes = self._update_enc_node_list(new_member, hiera_prefix=haproxy_prefix)

        if other_nodes:
            other_nodes_remote = self.spicerack.remote().query(f"D{{{','.join(other_nodes)}}}", use_sudo=True)
            LOGGER.info(
                "Running Puppet on %s existing nodes to add required Keepalived configuration", len(other_nodes_remote)
            )
            self.spicerack.puppet(other_nodes_remote).run()

        LOGGER.info("Now running Puppet to start keepalived on new server")
        puppet.enable(puppet_reason)
        puppet.run()
