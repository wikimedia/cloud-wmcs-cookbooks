"""Base classes for implementing cookbooks that run batch operations on OpenStack related servers."""
from __future__ import annotations

import argparse
from abc import ABCMeta, abstractmethod

from spicerack import RemoteHosts, Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import get_node_cluster_name


class CloudvirtBatchBase(CookbookBase, metaclass=ABCMeta):
    """Base cookbook class for batch operations on cloudvirt nodes."""

    def argument_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)

        parser.add_argument(
            "--fqdn",
            help="Operate on this specific node",
        )
        parser.add_argument(
            "--cluster-name",
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="Operate on all nodes of this cluster",
        )
        parser.add_argument(
            "--ceph-only",
            action="store_true",
            help="Operate on ceph-enabled nodes only",
        )
        # TODO: add support for selecting on e.g. kernel version or similar

        return parser


class CloudvirtBatchRunnerBase(WMCSCookbookRunnerBase, metaclass=ABCMeta):
    """Base cookbook runner class for batch operations on cloudvirt nodes."""

    def __init__(
        self,
        common_opts: CommonOpts,
        args: argparse.Namespace,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts

        if args.fqdn:
            self.query = f"D{{{args.fqdn}}}"
            self.cluster = get_node_cluster_name(args.fqdn)
        elif args.cluster_name:
            self.cluster = args.cluster_name
            self.query = (
                f"P{{O:wmcs::openstack::{self.cluster.value}::virt_ceph}}"
                if args.ceph_only
                else f"P{{P:openstack::{self.cluster.value}::nova::compute::service}}"
            )
        else:
            raise ValueError("Either --fqdn or --cluster-name must be specified")

        super().__init__(spicerack=spicerack, common_opts=common_opts)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"on hosts matched by '{self.query}'"

    def run_batch_operation(self) -> int | None:
        result = self.spicerack.remote().query(self.query, use_sudo=True)
        # TODO: make batch size configurable
        for hosts in result.split(len(result)):
            self.run_on_hosts(hosts)
        return 0

    def run_with_proxy(self) -> int | None:
        # With proxy for PuppetDB access.
        return self.run_batch_operation()

    @abstractmethod
    def run_on_hosts(self, hosts: RemoteHosts) -> None:
        pass
