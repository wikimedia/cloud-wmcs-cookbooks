r"""Downtime a single host and reboot it

- Set Icinga downtime
- Reboot
- Wait for host to come back online
- Remove the Icinga downtime after the host has been rebooted and the
  first Puppet run is complete

This is meant for one off servers and doesn't support pooling/depooling
clustered services (yet).

Usage example:
    cookbook sre.hosts.reboot-single sretest1001.eqiad.wmnet

"""
import argparse
import logging

from datetime import datetime, timedelta


__title__ = 'Downtime a single host and reboot it'
logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


def argument_parser():
    """Parse arguments"""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('host', help='A single host to be rebooted (specified in Cumin query syntax)')
    return parser


def run(args, spicerack):
    """Reboot the host"""
    remote_host = spicerack.remote().query(args.host)

    if len(remote_host) != 1:
        logger.error('Only a single server can be rebooted')
        return 1

    icinga = spicerack.icinga()
    puppet = spicerack.puppet(remote_host)
    reason = spicerack.admin_reason('Rebooting host')

    with icinga.hosts_downtimed(remote_host.hosts, reason, duration=timedelta(minutes=20)):
        reboot_time = datetime.utcnow()
        remote_host.reboot()
        remote_host.wait_reboot_since(reboot_time)
        puppet.wait_since(reboot_time)
        if not icinga.get_status(remote_host.hosts).optimal:
            logger.warning('Not all Icinga checks are fully recovered')