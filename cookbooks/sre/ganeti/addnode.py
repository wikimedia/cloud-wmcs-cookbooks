"""Add a new node to a Ganeti cluster"""

import argparse
import logging

from wmflib.interactive import ask_confirmation, ensure_shell_is_durable
from spicerack.cookbook import CookbookBase, CookbookRunnerBase
from cookbooks import ArgparseFormatter
from cookbooks.sre.ganeti import get_locations


logger = logging.getLogger(__name__)


class GanetiAddNode(CookbookBase):
    """Add a new node to a Ganeti cluster

    Validate various preconditions which need to happen to add a new node to
    a Ganeti cluster and eventually add it.

    Usage example:
        cookbook sre.ganeti.addnode eqsin ganeti5004.eqsin.wmnet
    """

    def argument_parser(self):
        """Parse command-line arguments for this module per spicerack API."""
        parser = argparse.ArgumentParser(description=self.__doc__,
                                         formatter_class=ArgparseFormatter)

        parser.add_argument('cluster', choices=sorted(get_locations().keys()),
                            help='The Ganeti cluster to which the new node should be added.')
        parser.add_argument('fqdn', help='The FQDN of the new Ganeti node.')

        return parser

    def get_runner(self, args):
        """As specified by Spicerack API."""
        return GanetiAddNodeRunner(args, self.spicerack)


class GanetiAddNodeRunner(CookbookRunnerBase):
    """Add a new node to a Ganeti cluster runner"""

    def __init__(self, args, spicerack):
        """Add a new node to a Ganeti cluster."""
        self.cluster, self.row, self.datacenter = get_locations()[args.location]
        ganeti = spicerack.ganeti()
        self.remote = spicerack.remote()
        self.master = self.remote.query(ganeti.rapi(self.cluster).master)
        self.remote_host = spicerack.remote.query(args.fqdn)
        self.fqdn = args.fqdn

        ensure_shell_is_durable()

        if len(self.remote_host) == 0:
            raise RuntimeError('Specified server not found, bailing out')

        if len(self.remote_host) != 1:
            raise RuntimeError('Only a single server can be added at a time')

    @property
    def runtime_description(self):
        """Return a nicely formatted string that represents the cookbook action."""
        return 'for new host {} to {}'.format(self.fqdn, self.cluster)

    def validate_state(self, cmd, msg):
        """Ensure a given precondition for adding a Ganeti node and bail out if missed"""
        try:
            status = next(self.master.run_sync(cmd))

        except StopIteration:
            status = None

        if not status:
            raise RuntimeError(
                '{} {}. Please fix and re-run the cookbook'.format(self.fqdn, msg)
            )

    def run(self):
        """Add a new node to a Ganeti cluster."""
        print('Ready to add Ganeti node {} in the {} cluster'.format(self.fqdn, self.master))
        ask_confirmation('Is this correct?')

        if str(self.remote_host) not in self.remote.query('A:ganeti-all').hosts:
            raise RuntimeError(
                '{} does have not have the Ganeti role applied. Please fix and re-run the cookbook'
                .format(self.fqdn)
            )

        self.validate_state(
            'lscpu |grep vmx',
            'does have not have virtualisation enabled in BIOS'
        )

        self.validate_state(
            'vgs | grep "ganeti "',
            ('No "ganeti" volume group found. You need to remove the swap device on /dev/md2, '
             'create a PV on /dev/md2 and eventually create a VG named "ganeti". Make sure to '
             'remove the stale swap entry from fstab as well'),
        )

        self.validate_state(
            'brctl show private | grep eno1',
            'No private bridge configured',
        )

        self.validate_state(
            'brctl show public | grep eno1',
            'No public bridge configured',
        )

        self.master.run_sync('gnt-node add "{node}"'.format(node=self.fqdn))
        ask_confirmation('Has the node been added correctly?')

        self.master.run_sync('gnt-cluster verify')
        ask_confirmation('Verify that the cluster state looks correct.')

        self.master.run_sync('gnt-cluster verify-disks')
        ask_confirmation('Verify that the disk state looks correct.')