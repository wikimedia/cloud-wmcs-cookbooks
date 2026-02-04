"""WMCS openstack - restart openstack services"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase, CookbookRunnerBase

from wmcs_libs.common import (
    CommonOpts,
    SALLogger,
    WMCSCookbookRunnerBase,
    add_common_opts,
    run_one_raw,
    with_common_opts,
)
from wmcs_libs.inventory.openstack import OpenstackClusterName
from wmcs_libs.openstack.common import get_rabbit_nodes

LOGGER = logging.getLogger(__name__)


class RebuildRabbit(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):
        parser = super().argument_parser()
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="Openstack cluster/deployment to act on.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> CookbookRunnerBase:
        return with_common_opts(spicerack=self.spicerack, args=args, runner=RebuildRabbitRunner)(
            spicerack=self.spicerack,
            cluster_name=args.cluster_name,
            args=args,
        )


class RebuildRabbitRunner(WMCSCookbookRunnerBase):
    def __init__(
        self,
        spicerack: Spicerack,
        cluster_name: OpenstackClusterName,
        args: argparse.Namespace,
        common_opts: CommonOpts,
    ):
        self.common_opts = common_opts
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)
        self.cluster_name = cluster_name
        self.args = args
        self.nova_services = None
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.rabbit_nodes = get_rabbit_nodes(cluster_name=cluster_name)

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"on deployment {self.cluster_name}"

    def reset_rabbit_cluster(self):
        nodedicts = []
        puppet_reason = self.spicerack.admin_reason("rabbitmq cluster rebuild in progress")
        for node in self.rabbit_nodes:
            remote = self.spicerack.remote().query(f"D{{{node}}}", use_sudo=True)
            puppet = self.spicerack.puppet(remote)
            puppet.disable(puppet_reason)
            nodedicts.append({"fqdn": node, "remote": remote, "puppet": puppet})

        # Order isn't super critical here but let's try to treat the first-indexed node
        #  as the primary node for repeatability
        for nodedict in reversed(nodedicts):
            run_one_raw(node=nodedict["remote"], command=["rabbitmqctl", "stop_app"])
            run_one_raw(node=nodedict["remote"], command=["rabbitmqctl", "reset"])
            run_one_raw(node=nodedict["remote"], command=["rabbitmqctl", "force_reset"])

        # that's a clean slate, now start one node and join the others to it.
        host_string = run_one_raw(node=nodedicts[0]["remote"], command=["rabbitmqctl", "start_app"])
        # host_string will be something like
        # "Starting node rabbit@cloudrabbit2001-dev.private.codfw.wikimedia.cloud ..."
        controller_fqdn = host_string.split("@")[1].split(" ")[0]

        # Puppet will create the queues we need on this node
        nodedicts[0]["puppet"].enable(puppet_reason)
        nodedicts[0]["puppet"].run()

        # That's one node all ready to go. Now join the other two to it.
        controller_clustername = f"rabbit@{controller_fqdn}"
        for nodedict in nodedicts[1:]:
            run_one_raw(node=nodedict["remote"], command=["rabbitmqctl", "join_cluster", controller_clustername])
            run_one_raw(node=nodedict["remote"], command=["rabbitmqctl", "start_app"])
            nodedict["puppet"].enable(puppet_reason)

    def run_with_proxy(self) -> None:
        self.reset_rabbit_cluster()
