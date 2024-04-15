"""Base classes for implementing cookbooks that run batch operations on OpenStack related servers."""

from __future__ import annotations

import argparse
from abc import ABCMeta

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.batch import WMCSCookbookBatchRunnerBase
from wmcs_libs.common import CommonOpts, add_common_opts
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import get_node_cluster_name


class CloudcontrolBatchBase(CookbookBase, metaclass=ABCMeta):
    """Base cookbook class for batch operations on clouducontrol nodes."""

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

        return parser


class CloudcontrolBatchRunnerBase(WMCSCookbookBatchRunnerBase, metaclass=ABCMeta):
    """Base cookbook runner class for batch operations on cloudvirt nodes."""

    def __init__(
        self,
        common_opts: CommonOpts,
        args: argparse.Namespace,
        spicerack: Spicerack,
    ):
        """Init"""
        super().__init__(common_opts, spicerack)
        if args.fqdn:
            self.cluster = get_node_cluster_name(args.fqdn)
            self.query = f"D{{{args.fqdn}}}"
        elif args.cluster_name:
            self.cluster = args.cluster_name
            self.query = f"P{{O:wmcs::openstack::{self.cluster.value}::control}}"
        else:
            raise ValueError("Either --fqdn or --cluster-name must be specified")


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


class CloudvirtBatchRunnerBase(WMCSCookbookBatchRunnerBase, metaclass=ABCMeta):
    """Base cookbook runner class for batch operations on cloudvirt nodes."""

    def __init__(
        self,
        common_opts: CommonOpts,
        args: argparse.Namespace,
        spicerack: Spicerack,
    ):
        """Init"""
        super().__init__(common_opts, spicerack)
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
