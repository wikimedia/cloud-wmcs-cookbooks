"""Upgrade/downgrade Varnish on the given cache host between major releases.

- Set Icinga downtime
- Depool
- Disable puppet (unless invoked with --hiera-merged)
- Wait for admin to merge hiera puppet change (unless invoked with --hiera-merged)
- Remove packages
- Re-enable puppet and run it to upgrade/downgrade
- Run a test request through frontend and backend
- Repool
- Remove Icinga downtime

Usage example:
    cookbook sre.hosts.upgrade-varnish --hiera-merged "Upgrading varnish -- TXXXXXX" cp3030.esams.wmnet

"""
import argparse
import logging
import time

from datetime import timedelta

import requests

__title__ = "Upgrade/downgrade Varnish on a cache host."
logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


def argument_parser():
    """As specified by Spicerack API."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("host", help="FQDN of the host to act upon.")
    parser.add_argument(
        "--downgrade",
        action="store_true",
        help="Downgrade varnish instead of upgrading it",
    )
    parser.add_argument(
        "--hiera-merged",
        metavar="COMMIT MESSAGE",
        help=(
            "Pass this flag if hiera is already updated "
            "and puppet is disabled on the host with this message"
        ),
    )
    return parser


def check_http_responses(host):
    """Check varnish-fe/varnish-be response.

    Returns:
        bool: False if either Varnish Frontend or Varnish Backend respond with an HTTP status code other than 200/404.

    """
    req = requests.head("http://{}".format(host), timeout=3)
    if req.status_code not in (requests.codes['ok'], requests.codes['not_found']):
        logger.error(
            "Unexpected response from varnish-fe. "
            "Got {} instead of 200/404. Exiting.".format(req.status_code)
        )
        return False

    req = requests.head("http://{}:3128".format(host), timeout=3)
    if req.status_code not in (requests.codes['ok'], requests.codes['not_found']):
        logger.error(
            "Unexpected response from varnish-be. "
            "Got {} instead of 200/404. Exiting.".format(req.status_code)
        )
        return False

    return True


def run(args, spicerack):
    """Required by Spicerack API."""
    remote_host = spicerack.remote().query(args.host)
    icinga = spicerack.icinga()
    puppet = spicerack.puppet(remote_host)

    action = "Upgrading"
    if args.downgrade:
        action = "Downgrading"

    reason = spicerack.admin_reason("{} Varnish".format(action))

    icinga.downtime_hosts(remote_host.hosts, reason, duration=timedelta(minutes=20))

    if not args.hiera_merged:
        # Check that puppet is not already disabled. We skip this check if
        # invoked with --hiera-merged because in that case puppet must
        # necessarily be disabled already. If that were not the case, it would
        # fail because of the discrepancy between the hiera setting
        # profile::cache::base::varnish_version and the Varnish version
        # installed on the system.
        puppet.check_enabled()
        puppet.disable(reason)
        spicerack.interactive.ask_confirmation(
            "Waiting for you to puppet-merge "
            "the change toggling {}'s hiera settings".format(args.host)
        )

    else:
        logger.info(
            "Not disabling puppet/waiting for puppet merge as requested (--hiera-merged)"
        )
        puppet.check_disabled()

    # Depool and wait a bit for the host to be drained
    remote_host.run_sync("depool")
    logger.info("Waiting for %s to be drained.", args.host)
    time.sleep(30)

    # Stop services, remove varnish
    cmds = [
        "apt update",
        "service varnish-frontend stop",
        "service varnish stop",
        "apt-get -y remove libvarnishapi1",
    ]
    remote_host.run_sync(*cmds)

    if args.hiera_merged:
        # If invoked with --hiera-merged we need to use the reason passed to
        # --hiera-merged itself in order to re-enable puppet
        reason = args.hiera_merged

    puppet.run(enable_reason=reason)

    # Post-puppet
    cmds = [
        "systemctl restart varnish.service",
        "run-puppet-agent",
        "systemctl restart prometheus-varnish-exporter@frontend.service",
    ]
    remote_host.run_sync(*cmds)

    # check HTTP response from backend/frontend
    if not check_http_responses(args.host):
        return 1

    # Repool and cancel Icinga downtime
    remote_host.run_sync("pool")
    icinga.remove_downtime(remote_host.hosts)
