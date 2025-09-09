r"""WMCS Toolforge - Add a new k8s node to a Toolforge cluster.

Usage example:
    cookbook wmcs.toolforge.add_k8s_node \
        --cluster-name toolsbeta \
        --role worker
"""

# pylint: disable=too-many-arguments
from __future__ import annotations

import argparse
import datetime
import logging

from cumin.transports import Command
from spicerack import Spicerack
from spicerack.cookbook import CookbookBase
from spicerack.puppet import PuppetHosts
from spicerack.remote import RemoteHosts

from cookbooks.wmcs.vps.create_instance_with_prefix import CreateInstanceWithPrefix
from cookbooks.wmcs.vps.refresh_puppet_certs import RefreshPuppetCerts
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, run_one_raw
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_cluster_node_prefix,
    get_cluster_node_server_group_name,
    get_cluster_security_group_name,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubeadm import KubeadmController
from wmcs_libs.k8s.kubernetes import KubernetesController
from wmcs_libs.openstack.common import OpenstackServerGroupPolicy
from wmcs_libs.openstack.enc import Enc

LOGGER = logging.getLogger(__name__)


class ToolforgeAddK8sNode(CookbookBase):
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
            default="debian-12.0-bookworm",
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
        parser.add_argument(
            "--role",
            required=True,
            choices=[role for role in ToolforgeKubernetesNodeRoleName if role.runs_kubelet],
            type=ToolforgeKubernetesNodeRoleName.from_str,
            help="Role of the node to create",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeAddK8sNodeRunner,
        )(
            image=args.image,
            flavor=args.flavor,
            network=args.network,
            role=args.role,
            spicerack=self.spicerack,
        )


class ToolforgeAddK8sNodeRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        spicerack: Spicerack,
        image: str | None,
        flavor: str | None,
        network: str | None,
        role: ToolforgeKubernetesNodeRoleName,
    ):

        self.common_opts = common_opts
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.image = image
        self.flavor = flavor
        self.network = network
        self.role = role

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for a {self.role.value} role in the {self.cluster_name.value} cluster"

    def _prepare_storage(self, node: RemoteHosts):
        if not self.role.has_extra_image_storage:
            return

        device = "/dev/sdb"
        LOGGER.info("Making sure %s is ext4, container overlay storage needs it", device)
        run_one_raw(
            node=node,
            # we have to remove the mount from fstab as the fstype will be wrong
            command=Command(
                f"grep '{device}.*ext4' /proc/mounts "
                "|| { "
                f"    sudo umount {device} 2>/dev/null; "
                f"    sudo -i mkfs.ext4 {device}; "
                f"    sudo sed -i -e '\\:^.*/var/lib/\\(docker\\|containerd\\)\\s.*:d' /etc/fstab; "
                "}"
            ),
        )

    def _update_hiera(self, new_node_fqdn: str):
        if not self.role.list_in_hiera:
            return

        hiera_role, hiera_key = self.role.list_in_hiera
        hiera_prefix = get_cluster_node_prefix(self.cluster_name, hiera_role)
        LOGGER.info("Updating Hiera key '%s' in prefix '%s'", hiera_key, hiera_prefix)

        enc = Enc(remote=self.spicerack.remote(), cluster_name=self.cluster_name.get_openstack_cluster_name())
        enc_prefix = enc.prefix(
            self.cluster_name.get_project(),
            hiera_prefix,
        )

        hiera = enc_prefix.get_current_hiera()
        hiera[hiera_key].append(new_node_fqdn)
        enc_prefix.set_hiera_values(hiera)

    def _tag_node(self, kubernetes_controller: KubernetesController, node: str):
        """Apply any necessary labels and taints."""
        # TODO: investigate if we can apply these in the Kubelet config file so
        # they are there from the first startup
        if self.role.labels:
            kubernetes_controller.add_node_labels(node_hostname=node, labels=self.role.labels)
        if self.role.taints:
            kubernetes_controller.add_node_taints(node_hostname=node, taints=self.role.taints)

    def run(self) -> None:

        node_prefix = get_cluster_node_prefix(self.cluster_name, self.role)
        security_group = get_cluster_security_group_name(self.cluster_name)
        server_group = get_cluster_node_server_group_name(self.cluster_name, self.role)

        start_args = [
            "--prefix",
            node_prefix,
            "--security-group",
            security_group,
            "--server-group",
            server_group,
            "--server-group-policy",
            OpenstackServerGroupPolicy.SOFT_ANTI_AFFINITY.value,
        ] + self.common_opts.to_cli_args()

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
        node = self.spicerack.remote().query(f"D{{{new_member.server_fqdn}}}", use_sudo=True)

        self._prepare_storage(node)

        LOGGER.info("Making sure that the proper puppetmaster is setup for the new node %s", new_member.server_fqdn)
        LOGGER.info("It might fail before rebooting, will make sure it runs after too.")
        refresh_puppet_certs_cookbook = RefreshPuppetCerts(spicerack=self.spicerack)
        refresh_puppet_certs_cookbook.get_runner(
            args=refresh_puppet_certs_cookbook.argument_parser().parse_args(
                ["--fqdn", new_member.server_fqdn, "--pre-run-puppet", "--ignore-failures"]
            ),
        ).run()

        LOGGER.info(
            (
                "Rebooting %s node %s to make sure iptables alternatives "
                "are taken into account by docker, kube-proxy and calico."
            ),
            self.role,
            new_member.server_fqdn,
        )

        reboot_time = datetime.datetime.utcnow()
        node.reboot()
        node.wait_reboot_since(since=reboot_time)

        LOGGER.info(
            "Rebooted node %s, running puppet again, this time it should work.",
            new_member.server_fqdn,
        )
        PuppetHosts(remote_hosts=node).run()

        kubeadm = KubeadmController(remote=self.spicerack.remote(), target_node_fqdn=new_member.server_fqdn)

        k8s_control_node_fqdn = get_control_nodes(self.cluster_name)[0]
        kubectl = KubernetesController(remote=self.spicerack.remote(), controlling_node_fqdn=k8s_control_node_fqdn)
        is_control = self.role == ToolforgeKubernetesNodeRoleName.CONTROL

        if is_control:
            etcd_nodes = kubeadm.get_etcd_nodes(existing_control_node_fqdn=k8s_control_node_fqdn)
            etcd_remote = self.spicerack.remote().query(f"D{{{','.join(etcd_nodes)}}}", use_sudo=True)
            LOGGER.info("Running Puppet on %s etcd nodes to pick up firewall changes", len(etcd_nodes))
            PuppetHosts(remote_hosts=etcd_remote).run()

            LOGGER.info("Copying CA data to the new server")
            kubeadm.copy_certificates_from(existing_node_fqdn=k8s_control_node_fqdn)

        LOGGER.info("Joining the cluster...")
        kubeadm.join(kubernetes_controller=kubectl, wait_for_ready=True, is_control=is_control)

        self._tag_node(kubernetes_controller=kubectl, node=new_member.server_fqdn.split(".")[0])
        self._update_hiera(new_node_fqdn=new_member.server_fqdn)

        self.spicerack.sal_logger.info("Added a new k8s %s %s to the cluster", self.role.value, new_member.server_fqdn)
