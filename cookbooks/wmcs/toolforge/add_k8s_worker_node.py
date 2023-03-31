r"""WMCS Toolforge - Add a new k8s worker node to a toolforge installation.

Usage example:
    cookbook wmcs.toolforge.add_k8s_worker_node \
        --cluster toolsbeta
"""
# pylint: disable=too-many-arguments
from __future__ import annotations

import argparse
import datetime
import logging

from cumin.transports import Command
from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from spicerack.puppet import PuppetHosts

from cookbooks.wmcs.vps.create_instance_with_prefix import CreateInstanceWithPrefix
from cookbooks.wmcs.vps.refresh_puppet_certs import RefreshPuppetCerts
from wmcs_libs.common import CommonOpts, SALLogger, WMCSCookbookRunnerBase, run_one_raw
from wmcs_libs.inventory import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_cluster_node_prefix,
    get_cluster_security_group_name,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubeadm import KubeadmController
from wmcs_libs.k8s.kubernetes import KubernetesController
from wmcs_libs.openstack.common import OpenstackServerGroupPolicy

LOGGER = logging.getLogger(__name__)


class ToolforgeAddK8sWorkerNode(CookbookBase):
    """WMCS Toolforge cookbook to add a new worker node"""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
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

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(self.spicerack, args, ToolforgeAddK8sWorkerNodeRunner,)(
            k8s_worker_prefix=args.k8s_worker_prefix,
            k8s_control_prefix=args.k8s_control_prefix,
            image=args.image,
            flavor=args.flavor,
            spicerack=self.spicerack,
        )


class ToolforgeAddK8sWorkerNodeRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeAddK8sWorkerNode"""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        k8s_worker_prefix: str | None,
        k8s_control_prefix: str | None,
        spicerack: Spicerack,
        image: str | None = None,
        flavor: str | None = None,
    ):
        """Init"""
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.k8s_worker_prefix = k8s_worker_prefix
        self.k8s_control_prefix = k8s_control_prefix
        super().__init__(spicerack=spicerack)
        self.image = image
        self.flavor = flavor
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )

    def run(self) -> None:
        """Main entry point"""
        self.sallogger.log(message="Adding a new k8s worker node")

        node_prefix = get_cluster_node_prefix(self.cluster_name, ToolforgeKubernetesNodeRoleName.WORKER)
        security_group = get_cluster_security_group_name(self.cluster_name)

        start_args = [
            "--prefix",
            node_prefix,
            "--security-group",
            security_group,
            "--server-group",
            node_prefix,
            "--server-group-policy",
            OpenstackServerGroupPolicy.SOFT_ANTI_AFFINITY.value,
        ] + self.common_opts.to_cli_args()

        if self.image:
            start_args.extend(["--image", self.image])

        if self.flavor:
            start_args.extend(["--flavor", self.flavor])

        create_instance_cookbook = CreateInstanceWithPrefix(spicerack=self.spicerack)
        new_member = create_instance_cookbook.get_runner(
            args=create_instance_cookbook.argument_parser().parse_args(start_args)
        ).create_instance()
        node = self.spicerack.remote().query(f"D{{{new_member.server_fqdn}}}", use_sudo=True)

        device = "/dev/sdb"
        LOGGER.info("Making sure %s is ext4, docker overlay storage needs it", device)
        run_one_raw(
            node=node,
            # we have to remove the mount from fstab as the fstype will be wrong
            command=Command(
                f"grep '{device}.*ext4' /proc/mounts "
                "|| { "
                f"    sudo umount {device} 2>/dev/null; "
                f"    sudo -i mkfs.ext4 {device}; "
                f"    sudo sed -i -e '\\|^.*/var/lib/docker\\s.*|d' /etc/fstab; "
                "}"
            ),
        )

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
                "Rebooting worker node %s to make sure iptables alternatives "
                "are taken into account by docker, kube-proxy and calico."
            ),
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

        kubeadm = KubeadmController(remote=self.spicerack.remote(), controlling_node_fqdn=new_member.server_fqdn)

        k8s_control_node_fqdn = get_control_nodes(self.cluster_name)[0]
        kubectl = KubernetesController(remote=self.spicerack.remote(), controlling_node_fqdn=k8s_control_node_fqdn)

        LOGGER.info("Joining the cluster...")
        kubeadm.join(kubernetes_controller=kubectl, wait_for_ready=True)

        self.sallogger.log(message=f"Added a new k8s worker {new_member.server_fqdn} to the worker pool")
