"""Downtime a single Ganeti VM and reboot it on the Ganeti level"""

import argparse
import logging
import time

from datetime import timedelta

from spicerack.cookbook import CookbookBase, CookbookRunnerBase
from spicerack.icinga import IcingaError
from cookbooks.sre import PHABRICATOR_BOT_CONFIG_FILE
from cookbooks.sre.ganeti import get_locations

logger = logging.getLogger(__name__)


class RebootSingleVM(CookbookBase):
    """Downtime a single Ganeti VM and reboot it on the Ganeti level

       This is different from a normal reboot triggered on the OS level,
       it can be compared to powercycling a server. This kind of reboot
       is e.g. needed if KVM/QEMU machine settings have been modified.

    - Set Icinga downtime
    - Reboot with optional depool
    - Wait for VM to come back online
    - Remove the Icinga downtime after the VM has been rebooted, the
      first Puppet run is complete and all Icinga checks have recovered.

    Usage example:
        cookbook sre.ganeti.reboot-vm failoid1002.eqiad.wmnet

    """

    def get_runner(self, args):
        """As specified by Spicerack API."""
        return RebootSingleVMRunner(args, self.spicerack)

    def argument_parser(self):
        """Parse arguments"""
        parser = argparse.ArgumentParser(description=self.__doc__,
                                         formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument('vm', help='A single VM to reboot (specified in Cumin query syntax)')
        parser.add_argument('location', choices=sorted(get_locations().keys()),
                            help='The datacenter and row (only for multi-row clusters) where to VM runs.')
        parser.add_argument('-r', '--reason', required=False,
                            help=('The reason for the reboot. The current username and originating'
                                  'Cumin host are automatically added.'))
        parser.add_argument('-t', '--task-id',
                            help='An optional task ID to refer in the downtime message.')
        parser.add_argument('--depool', help='Whether to run depool/pool on the VM around reboots.',
                            action='store_true')
        return parser


class RebootSingleVMRunner(CookbookRunnerBase):
    """Downtime a single VM and reboot it runner."""

    def __init__(self, args, spicerack):
        """Downtime a single VM and reboot it"""
        self.remote_host = spicerack.remote().query(args.vm)
        self.remote = spicerack.remote()
        self.cluster, self.row, self.datacenter = get_locations()[args.location]
        ganeti = spicerack.ganeti()
        self.master = self.remote.query(ganeti.rapi(self.cluster).master)

        if len(self.remote_host) == 0:
            raise RuntimeError('Specified VM not found, bailing out')

        if len(self.remote_host) != 1:
            raise RuntimeError('Only a single VM can be rebooted')

        self.icinga_hosts = spicerack.icinga_hosts(self.remote_host.hosts)
        self.puppet = spicerack.puppet(self.remote_host)
        self.reason = spicerack.admin_reason('Rebooting VM' if not args.reason else args.reason)

        if args.task_id is not None:
            self.phabricator = spicerack.phabricator(PHABRICATOR_BOT_CONFIG_FILE)
            self.task_id = args.task_id
            self.message = ('VM {vm} rebooted by {owner} with reason: {reason}\n').format(
                vm=self.remote_host, owner=self.reason.owner, reason=args.reason)
        else:
            self.phabricator = None

        self.depool = args.depool

    @property
    def runtime_description(self):
        """Return a nicely formatted string that represents the cookbook action."""
        return 'for VM {}'.format(self.remote_host)

    def run(self):
        """Reboot the VM"""
        with self.icinga_hosts.downtimed(self.reason, duration=timedelta(minutes=20)):
            if self.phabricator is not None:
                self.phabricator.task_comment(self.task_id, self.message)

            if self.depool:
                self.remote_host.run_async('depool')
                logger.info('Waiting a 30 second grace period after depooling')
                time.sleep(30)
            self.master.run_sync('/usr/sbin/gnt-instance reboot "{vm}"'.format(vm=self.remote_host))

            try:
                self.icinga_hosts.wait_for_optimal()
                icinga_ok = True
            except IcingaError:
                logger.error(
                    "The VM's status is not optimal according to Icinga, "
                    "please check it.")

            if self.depool:
                if icinga_ok:
                    self.remote_host.run_async('pool')
                else:
                    logger.warning(
                        "NOT repooling the services due to the VM's Icinga status.")
