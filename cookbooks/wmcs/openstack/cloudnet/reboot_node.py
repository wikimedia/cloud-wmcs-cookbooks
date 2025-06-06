r"""WMCS Openstack - Reboot a cloudnet node .

Usage example:
    cookbook wmcs.openstack.cloudnet.reboot_node \
    --fqdn-to-reboot cloudnet1004.eqiad.wmnet

"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta

from spicerack import RemoteHosts, Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase, add_common_opts, with_common_opts
from wmcs_libs.openstack.common import NeutronAgentType, OpenstackAPI, get_node_cluster_name
from wmcs_libs.openstack.neutron import NetworkUnhealthy, NeutronAlerts, NeutronController

LOGGER = logging.getLogger(__name__)


class RebootNode(CookbookBase):
    """WMCS Openstack cookbook to reboot a single cloudnets, handling failover."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser)
        parser.add_argument(
            "--fqdn-to-reboot",
            required=True,
            help="FQDN of the node to reboot.",
        )
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="If passed, will continue even if the cluster is not in a healthy state.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(
            self.spicerack,
            args,
            RebootNodeRunner,
        )(
            fqdn_to_reboot=args.fqdn_to_reboot,
            force=args.force,
            spicerack=self.spicerack,
        )


class RebootNodeRunner(WMCSCookbookRunnerBase):
    """Runner for RebootNode"""

    def __init__(
        self,
        common_opts: CommonOpts,
        fqdn_to_reboot: str,
        force: bool,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        self.fqdn_to_reboot = fqdn_to_reboot
        self.force = force

        super().__init__(spicerack=spicerack, common_opts=common_opts)

        cluster_name = get_node_cluster_name(node=self.fqdn_to_reboot)
        self.openstack_api = OpenstackAPI(
            remote=self.spicerack.remote(),
            cluster_name=cluster_name,
            project=self.common_opts.project,
        )
        self.neutron_controller = NeutronController(openstack_api=self.openstack_api)

        try:
            self.neutron_controller.check_if_network_is_alive()
        except NetworkUnhealthy as error:
            if not self.force:
                raise Exception(
                    "There's some agent down in the network, if you still want to reboot the nodes pass --force."
                ) from error

            LOGGER.warning("Some agents are down, will continue due to --force: \n%s", error)

    @property
    def runtime_description(self) -> str:
        return f"for host {self.fqdn_to_reboot}"

    def _do_reboot(self, node: RemoteHosts) -> None:
        """Perform the actual reboot."""
        host_name = self.fqdn_to_reboot.split(".", 1)[0]

        LOGGER.info("Taking the node out of the cluster (setting admin-state-down to all it's agents)")
        self.neutron_controller.cloudnet_set_admin_down(cloudnet_host=host_name)
        if not self.force:
            agents_on_cloudnet = self.openstack_api.get_neutron_agents(host=host_name)
            if any(agent.agent_type == NeutronAgentType.L3_AGENT for agent in agents_on_cloudnet):
                LOGGER.info("This is an L3 agent node, waiting for the router handover if needed...")
                self.neutron_controller.wait_for_l3_handover()
                LOGGER.info("Handover done.")
        else:
            LOGGER.warning("Skipping L3 handover due to --force passed.")

        reboot_time = datetime.utcnow()
        node.reboot()

        node.wait_reboot_since(since=reboot_time)
        LOGGER.info(
            "Rebooted node %s, waiting for cluster to stabilize...",
            self.fqdn_to_reboot,
        )

        LOGGER.info("Making the host %s admin up...", host_name)
        self.neutron_controller.cloudnet_set_admin_up(cloudnet_host=host_name)
        LOGGER.info("Host %s is admin up", host_name)

        if not self.force:
            LOGGER.info("Waiting, for all it's agents to be up and running...")
            self.neutron_controller.wait_for_network_alive()
            LOGGER.info("All agents up.")
            LOGGER.info("Node up and running, and all agents working! Removing alert silences...")
        else:
            LOGGER.warning("Skipping waiting for the network alive due to --force passed")

    def run_with_proxy(self) -> None:
        """Main entry point"""
        node = self.spicerack.remote().query(f"D{{{self.fqdn_to_reboot}}}", use_sudo=True)

        alertmanager = self.spicerack.alertmanager()
        alertmanager_hosts = self.spicerack.alertmanager_hosts(node.hosts)
        reason = self.spicerack.admin_reason(
            f"Rebooting {self.fqdn_to_reboot} with the wmcs.openstack.cloudnet.reboot_node cookbook",
            task_id=self.common_opts.task_id,
        )
        downtime_duration = timedelta(hours=1)

        with alertmanager_hosts.downtimed(reason=reason, duration=downtime_duration):
            with alertmanager.downtimed(
                reason=reason,
                duration=downtime_duration,
                matchers=[{"name": "alertname", "value": NeutronAlerts.NEUTRON_AGENT_DOWN.value, "isRegex": False}],
            ):
                self._do_reboot(node)
