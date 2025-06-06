"""WMCS openstack - restart openstack services"""

from __future__ import annotations

import argparse
import logging
from typing import List

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase, CookbookRunnerBase

from wmcs_libs.common import (
    CommonOpts,
    SALLogger,
    WMCSCookbookRunnerBase,
    add_common_opts,
    run_one_raw,
    with_common_opts,
)
from wmcs_libs.inventory.openstack import OpenstackClusterName, OpenstackNodeRoleName
from wmcs_libs.openstack.common import NeutronAgentType, OpenstackAPI

LOGGER = logging.getLogger(__name__)


class OpenstackRestart(CookbookBase):
    """WMCS Openstack cookbook to restart services."""

    __title__ = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(prog=__name__, description=__doc__, formatter_class=ArgparseFormatter)
        add_common_opts(parser)
        parser.add_argument(
            "--cluster-name",
            required=True,
            choices=list(OpenstackClusterName),
            type=OpenstackClusterName,
            help="Openstack cluster/deployment to act on.",
        )
        parser.add_argument("--all-services", action="store_true", help="Restart all openstack services")
        parser.add_argument("--nova", action="store_true", help="Restart all openstack nova services")
        parser.add_argument("--glance", action="store_true", help="Restart all openstack glance services")
        parser.add_argument("--keystone", action="store_true", help="Restart all openstack keystone services")
        parser.add_argument("--cinder", action="store_true", help="Restart all openstack cinder services")
        parser.add_argument(
            "--neutron", action="store_true", help="Restart all openstack neutron services except for neutron-l3-agent"
        )
        parser.add_argument("--trove", action="store_true", help="Restart all openstack trove services")
        parser.add_argument("--magnum", action="store_true", help="Restart all openstack magnum services")
        parser.add_argument("--octavia", action="store_true", help="Restart all openstack octavia services")
        parser.add_argument("--heat", action="store_true", help="Restart all openstack magnum services")
        parser.add_argument("--swift", action="store_true", help="Restart all openstack swift services")
        parser.add_argument("--designate", action="store_true", help="Restart all openstack swift services")

        parser.add_argument(
            "--filter-nodes",
            choices=[entry for entry in OpenstackNodeRoleName if entry != OpenstackNodeRoleName.GATEWAY],
            type=OpenstackNodeRoleName,
            nargs="+",
            help="Restart services only on this type of hosts",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> CookbookRunnerBase:
        """Get runner"""
        return with_common_opts(spicerack=self.spicerack, args=args, runner=OpenstackRestartRunner)(
            spicerack=self.spicerack,
            cluster_name=args.cluster_name,
            args=args,
            filter_nodes=args.filter_nodes,
        )


class OpenstackRestartRunner(WMCSCookbookRunnerBase):
    """Runner for OpenstackRestart"""

    def __init__(
        self,
        spicerack: Spicerack,
        cluster_name: OpenstackClusterName,
        args: argparse.Namespace,
        common_opts: CommonOpts,
        filter_nodes: List[OpenstackNodeRoleName],
    ):
        """Init"""
        self.common_opts = common_opts
        self.sallogger = SALLogger.from_common_opts(common_opts=common_opts)
        self.cluster_name = cluster_name
        self.args = args
        self.nova_services = None
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.openstack_api = OpenstackAPI(remote=spicerack.remote(), cluster_name=cluster_name)
        self.filter_nodes = filter_nodes

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        if self.args.all_services:
            services = "all services"
        else:
            services_set_args = []
            for arg in vars(self.args):
                if vars(self.args)[arg] and "_" not in arg:
                    services_set_args.append(arg)

            _services = ",".join(services_set_args)
            services = f"service: {_services}"

        return f"on deployment {self.cluster_name} for {services}"

    # OpenStack services will give us info about hosts and services, but in a different format
    #  depending on the service. These little helper functions adjust that into standard
    #  (host, service) pairs
    def get_nova_service_list(self):
        """Get a list of registered nova services + hosts from OpenStack"""
        # Cache in case this gets called twice
        if not self.nova_services:
            service_info = self.openstack_api.get_nova_services()
            self.nova_services = [(service["Host"], service["Binary"]) for service in service_info]
        return self.nova_services

    def get_designate_service_list(self):
        """Get a list of registered designate services + hosts from OpenStack"""
        service_info = self.openstack_api.get_designate_services()
        return [(service["hostname"], "designate-%s" % service["service_name"]) for service in service_info]

    def get_neutron_service_list(self):
        """Get a list of registered neutron services + hosts from OpenStack"""
        agents = self.openstack_api.get_neutron_agents()
        # We never want to automatically restart the l3 agents, that can cause downtime.
        return [(agent.host, agent.binary) for agent in agents if agent.agent_type != NeutronAgentType.L3_AGENT]

    def get_cinder_service_list(self):
        """Get a list of registered cinder services + hosts from OpenStack"""
        service_info = self.openstack_api.get_cinder_services()
        return [(service["Host"].removesuffix("@rbd"), service["Binary"]) for service in service_info]

    def get_misc_service_list(self, service):
        """Get a list of unregistered OpenStack services.

        There are several services that don't provide a useful discovery mechanism, all running on cloudcontrols.
         This function cheats and gets the cloudcontrols out of the nova service list,
         then hardcodes those services into the standard dict format.
        """
        cloudcontrol_service_list = {
            "cinder": ["cinder-api"],
            "glance": ["glance-api"],
            "nova": ["nova-api", "nova-api-metadata"],
            "keystone": ["keystone", "keystone-admin"],
            "trove": ["trove-api", "trove-conductor", "trove-taskmanager"],
            "heat": ["heat-api", "heat-api-cfn", "heat-engine"],
            "magnum": ["magnum-api", "magnum-conductor"],
            "octavia": ["octavia-api", "octavia-worker", "octavia-housekeeping", "octavia-health-manager"],
            "neutron": ["neutron-api", "neutron-rpc-server"],
        }

        cloudcontrols = {s[0] for s in self.get_nova_service_list() if s[0].startswith("cloudcontrol")}
        servicelist = []
        for servicename in cloudcontrol_service_list[service]:
            servicelist.extend([(cloudcontrol, servicename) for cloudcontrol in cloudcontrols])

        return servicelist

    def consolidate_restart_list(self, restart_list):
        """We want to make only one call per host. Fortunately, systemctl takes a list."""
        restart_dict = {}
        for pair in restart_list:
            if pair[0] not in restart_dict:
                restart_dict[pair[0]] = [pair[1]]
            else:
                restart_dict[pair[0]].append(pair[1])
        return restart_dict

    def restart_services(self, restart_dict: dict[str, list[str]]):
        """Restart services specified in a dict of hostname:[service]"""
        for host in restart_dict:
            # We still need to do a lookup because we didn't get fqdns from
            #  openstack.
            LOGGER.info("Restarting openstack services on %s: %s", host, restart_dict[host])
            try:
                query = "P{%s*}" % host
                nodes = self.spicerack.remote().query(query, use_sudo=True)
                command = ["systemctl", "restart"] + restart_dict[host]
                run_one_raw(node=nodes, command=command)
            except Exception:  # pylint: disable=broad-except
                LOGGER.warning("Failed to restart services on %s", host, exc_info=True)

    def run_with_proxy(self) -> None:
        """Main entry point"""
        restart_list = []
        if vars(self.args)["nova"] or self.args.all_services:
            restart_list.extend(self.get_nova_service_list())
            # Nova service discovery doesn't find the api services
            restart_list.extend(self.get_misc_service_list("nova"))
        if vars(self.args)["cinder"] or self.args.all_services:
            restart_list.extend(self.get_cinder_service_list())
        if vars(self.args)["neutron"] or self.args.all_services:
            # Neutron supports discovery for most, but not all
            #  of its services
            restart_list.extend(self.get_neutron_service_list())
            restart_list.extend(self.get_misc_service_list("neutron"))
        if vars(self.args)["designate"] or self.args.all_services:
            restart_list.extend(self.get_designate_service_list())
        if vars(self.args)["trove"] or self.args.all_services:
            restart_list.extend(self.get_misc_service_list("trove"))
        if vars(self.args)["keystone"] or self.args.all_services:
            restart_list.extend(self.get_misc_service_list("keystone"))
        if vars(self.args)["glance"] or self.args.all_services:
            restart_list.extend(self.get_misc_service_list("glance"))
        if vars(self.args)["magnum"] or self.args.all_services:
            restart_list.extend(self.get_misc_service_list("magnum"))
        if vars(self.args)["octavia"] or self.args.all_services:
            restart_list.extend(self.get_misc_service_list("octavia"))
        if vars(self.args)["heat"] or self.args.all_services:
            restart_list.extend(self.get_misc_service_list("heat"))

        if self.filter_nodes:
            restart_list = [
                entry
                for entry in restart_list
                if any(entry[0].startswith(prefix.value) for prefix in self.filter_nodes)
            ]

        if restart_list:
            restart_dict = self.consolidate_restart_list(restart_list)
            self.sallogger.log("Restarting %s openstack services" % len(restart_list))
            # Restart cloudvirt nodes last, there are a lot of them and
            #  restarts there have more local effect.
            cloudvirt_dict = {}
            for key in list(restart_dict.keys()):
                if key.startswith("cloudvirt"):
                    cloudvirt_dict[key] = restart_dict.pop(key)
            self.restart_services(restart_dict)
            self.restart_services(cloudvirt_dict)
        else:
            print("No restarts requested.")
