"""WMCS openstack - set a cloudvirt node in maintenance

Usage example: wmcs.openstack.cloudvirt.set_maintenance \
    --control-node-fqdn cloudcontrol1003.eqiad.wmnet
    --fqdn cloudvirt1013.eqiad.wmnet

"""
# pylint: disable=unsubscriptable-object,too-many-arguments
import argparse
import logging
from typing import Optional

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase, CookbookRunnerBase
from spicerack.icinga import Icinga, ICINGA_DOMAIN

from cookbooks.wmcs import OpenstackAPI, NotFound

LOGGER = logging.getLogger(__name__)


class SetMaintenance(CookbookBase):
    """WMCS Openstack cookbook to set a cloudvirt node in maintenance."""

    __title__ = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=self.__doc__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
        parser.add_argument(
            "--control-node-fqdn",
            required=True,
            help="FQDN of the control node to orchestrate from.",
        )
        parser.add_argument(
            "--fqdn",
            required=True,
            help="FQDN of the cloudvirt to set in maintenance.",
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> CookbookRunnerBase:
        """Get runner"""
        return SetMaintenanceRunner(
            fqdn=args.fqdn,
            control_node_fqdn=args.control_node_fqdn,
            spicerack=self.spicerack,
        )


class SetMaintenanceRunner(CookbookRunnerBase):
    """Runner for SetMaintenance."""

    def __init__(
        self,
        fqdn: str,
        control_node_fqdn: str,
        spicerack: Spicerack,
    ):
        """Init."""
        self.fqdn = fqdn
        self.control_node_fqdn = control_node_fqdn
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            control_node_fqdn=control_node_fqdn,
        )
        self.spicerack = spicerack

    def run(self) -> Optional[int]:
        """Main entry point."""
        icinga = Icinga(
            icinga_host=self.spicerack.remote().query(self.spicerack.dns().resolve_cname(ICINGA_DOMAIN), use_sudo=True)
        )
        icinga.downtime_hosts(
            hosts=[self.fqdn],
            reason=self.spicerack.admin_reason('Setting maintenance mode.')
        )
        hostname = self.fqdn.split('.', 1)[0]
        try:
            self.openstack_api.aggregate_remove_host(aggregate_name="ceph", host_name=hostname)
        except NotFound as error:
            logging.info("%s", error)

        try:
            self.openstack_api.aggregate_add_host(aggregate_name="maintenance", host_name=hostname)
        except NotFound as error:
            logging.info("%s", error)

        LOGGER.info("Host %s now in maintenance mode. No new VMs will be scheduled in it.", self.fqdn)