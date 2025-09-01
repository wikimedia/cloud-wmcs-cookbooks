r"""WMCS Toolforge Kubernetes - upgrade batches of k8s workers

Usage example:
    cookbook wmcs.toolforge.k8s.upgrade_workers \
        --cluster-name tools \
        --batch-number 1

    cookbook wmcs.toolforge.k8s.upgrade_workers \
        --cluster-name toolsbeta \
        --non-nfs-workers \
        --nfs-workers \
        --batches 2 \
        --batch-number 1

"""

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from wmflib.interactive import ask_confirmation

from cookbooks.wmcs.toolforge.k8s.worker.upgrade import UpgradeRunner
from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase
from wmcs_libs.inventory.toolsk8s import (
    ToolforgeKubernetesClusterName,
)
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.k8s.kubernetes import KubernetesController, validate_version
from wmcs_libs.openstack.common import OpenstackAPI, OpenstackClusterName

LOGGER = logging.getLogger(__name__)


class ToolforgeK8sUpgradeWorkers(CookbookBase):
    """Reboot k8s workers that are stuck."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        parser.add_argument(
            "--yes-i-know-what-im-doing",
            required=False,
            action="store_true",
            help="If passed, will not ask for confirmation.",
        )
        parser.add_argument(
            "--nfs-workers",
            required=False,
            action="store_true",
            help="If passed, it will add all the nfs workers to the list of hosts.",
        )
        parser.add_argument(
            "--non-nfs-workers",
            required=False,
            action="store_true",
            help="If passed, it will add all the non-nfs workers to the list of hosts.",
        )
        parser.add_argument(
            "--batches",
            required=False,
            default=3,
            type=int,
            help="Amount of workers to upgrade in parallel.",
        )
        parser.add_argument(
            "--batch-number",
            required=True,
            type=int,
            help=(
                "Which batch to run through. Use this to parallelize (open several terminals and use a different "
                "batch number on each) until we can run full cookbooks in parallel."
            ),
        )
        parser.add_argument(
            "--hosts",
            required=False,
            default="",
            help="Comma-separated list of workers to upgrade.",
        )
        parser.add_argument(
            "--src-version",
            required=False,
            type=validate_version,
            help="Old version to upgrade from, will autodetect if not passed.",
        )
        parser.add_argument(
            "--dst-version",
            required=True,
            type=validate_version,
            help="New version to migrate to.",
        )
        add_toolforge_kubernetes_cluster_opts(parser)
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeK8sUpgradeWorkersRunner,
        )(
            yes_i_know=args.yes_i_know_what_im_doing,
            non_nfs_workers=args.non_nfs_workers,
            nfs_workers=args.nfs_workers,
            hosts=args.hosts,
            batches=args.batches,
            batch_number=args.batch_number,
            src_version=args.src_version,
            dst_version=args.dst_version,
            spicerack=self.spicerack,
        )


class ToolforgeK8sUpgradeWorkersRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeK8sUpgradeWorkers."""

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        yes_i_know: bool,
        nfs_workers: bool,
        non_nfs_workers: bool,
        hosts: str,
        batches: int,
        batch_number: int,
        src_version: str,
        dst_version: str,
        spicerack: Spicerack,
    ):  # pylint: disable=too-many-arguments
        """Init"""
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.common_opts = common_opts
        self.cluster_name = cluster_name
        self.yes_i_know = yes_i_know
        self.domain = f"{self.common_opts.project}.eqiad1.wikimedia.cloud"
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=OpenstackClusterName.EQIAD1,
            project=self.common_opts.project,
        )
        self.batches = batches
        self.batch_number = batch_number
        self.src_version = src_version
        self.dst_version = dst_version

        self.remote = self.spicerack.remote()
        self.control_node_fqdn = KubernetesController.pick_a_control_node(cluster_name=self.cluster_name)
        LOGGER.info("Using control node %s", self.control_node_fqdn)
        self.k8s_controller = KubernetesController(remote=self.remote, controlling_node_fqdn=self.control_node_fqdn)

        self.hostname_list = [host.strip() for host in hosts.split(",")] if hosts else []
        all_nodes = self.k8s_controller.get_nodes_hostnames()
        if nfs_workers:
            self.hostname_list.extend(node for node in all_nodes if "worker-nfs-" in node)
        if non_nfs_workers:
            self.hostname_list.extend(node for node in all_nodes if "-nfs-" not in node and "-worker-" in node)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        if not self.hostname_list:
            return "no workers to upgrade found"
        return f"for {', '.join(self.hostname_list)}"

    def run_with_proxy(self) -> None:
        """Main entry point"""
        if not self.hostname_list:
            print(
                "No workers to upgrade passed, pass one or more of `--nfs-workers`, `--non-nfs-workers` or `--hosts`."
            )
            return

        batches = do_batch(items=self.hostname_list, batch_size=self.batches)
        my_batch = batches[self.batch_number - 1]

        if not self.yes_i_know:
            hosts_str = "\n* " + "\n* ".join(my_batch)
            ask_confirmation(
                f"Split the upgrade of {len(self.hostname_list)} workers in {self.batches} batches, I'm running "
                + f"batch number {self.batch_number}: {hosts_str}"
                + "\nAre you sure?"
            )

        for index, hostname in enumerate(my_batch):
            UpgradeRunner(
                cluster_name=self.cluster_name,
                spicerack=self.spicerack,
                common_opts=self.common_opts,
                dst_version=self.dst_version,
                src_version=self.src_version,
                hostname=hostname,
            ).run_with_proxy()
            print(
                f"## upgrade_workers: Upgraded {index + 1} of {len(my_batch)} nodes, {len(my_batch) - index - 1} left"
            )


def do_batch(items: list[str], batch_size: int) -> list[list[str]]:
    if batch_size <= 0:
        raise ValueError("can't have less than 1 batch")
    sorted_items = sorted(items)
    length = len(items)
    min_batch_size, number_of_remainders = divmod(length, batch_size)
    batches = []
    start = 0
    for batch_num in range(batch_size):
        remainder = 1 if batch_num < number_of_remainders else 0
        end = start + min_batch_size + remainder
        batches.append(sorted_items[start:end])
        start = end
    return batches
