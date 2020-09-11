"""Decommission a host from all inventories.

It works for both Physical and Virtual hosts.
If the query doesn't match any hosts allow to proceed with hostname expansion.

List of actions performed on each host:
- Check if any reference was left in the Puppet (both public and private) or mediawiki-config repositories and ask for
  confirmation before proceeding if there is any match
- Downtime the host on Icinga (it will be removed at the next Puppet run on the Icinga host)
- Detect if Physical or Virtual host based on Netbox data.
- If virtual host (Ganeti VM)
  - Ganeti shutdown (tries OS shutdown first, pulls the plug after 2 minutes)
  - Force Ganeti->Netbox sync of VMs to update its state and avoid Netbox Report errors
- If physical host
  - Downtime the management host on Icinga (it will be removed at the next Puppet run on the Icinga host)
  - Wipe bootloaders to prevent it from booting again
  - Pull the plug (IPMI power off without shutdown)
  - Update Netbox state to Decommissioning and delete all non-mgmt interfaces and related IPs
- Remove it from DebMonitor
- Remove it from Puppet master and PuppetDB
- If virtual host (Ganeti VM), issue a VM removal that will destroy the VM. Can take few minutes.
- Run the sre.dns.netbox cookbook if the DC DNS records have been migrated to the automated system or tell the user
  that a manual patch is required.
- Update the related Phabricator task

Usage example:
    cookbook sre.hosts.decommission -t T12345 mw1234.codfw.wmnet

"""
import argparse
import logging
import re
import time

from cumin.transports import Command

from spicerack.dns import DnsError
from spicerack.interactive import ask_confirmation
from spicerack.ipmi import IpmiError
from spicerack.puppet import get_puppet_ca_hostname
from spicerack.remote import NodeSet, RemoteError, RemoteExecutionError

from cookbooks.sre import PHABRICATOR_BOT_CONFIG_FILE
from cookbooks.sre.dns.netbox import argument_parser as dns_netbox_argparse, run as dns_netbox_run


__title__ = 'Decommission a host from all inventories.'
logger = logging.getLogger(__name__)  # pylint: disable=invalid-name
DEPLOYMENT_HOST = 'deployment.eqiad.wmnet'
MEDIAWIKI_CONFIG_REPO_PATH = '/srv/mediawiki-staging'
PUPPET_REPO_PATH = '/var/lib/git/operations/puppet'
PUPPET_PRIVATE_REPO_PATH = '/srv/private'
MIGRATED_PRIMARY_SITES = ()


def argument_parser():
    """As specified by Spicerack API."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('query', help=('Cumin query to match the host(s) to act upon. At most 5 at a time, with '
                                       '--force at most 20 at a time.'))
    parser.add_argument('-t', '--task-id', required=True, help='the Phabricator task ID (e.g. T12345)')
    parser.add_argument('--force', help='Bypass the default limit of 5 hosts at a time, but only up to 20 hosts.')

    return parser


def _decommission_host(fqdn, spicerack, reason):  # noqa: MC0001
    """Perform all the decommissioning actions on a single host."""
    hostname = fqdn.split('.')[0]
    icinga = spicerack.icinga()
    remote = spicerack.remote()
    puppet_master = spicerack.puppet_master()
    debmonitor = spicerack.debmonitor()
    netbox = spicerack.netbox(read_write=True)
    ganeti = spicerack.ganeti()

    # Using the Direct Cumin backend to support also hosts already removed from PuppetDB
    remote_host = remote.query('D{' + fqdn + '}')

    # Downtime on Icinga both the host and the mgmt host (later below), they will be removed by Puppet
    try:
        icinga.downtime_hosts([fqdn], reason)
        spicerack.actions[fqdn].success('Downtimed host on Icinga')
    except RemoteExecutionError:
        spicerack.actions[fqdn].failure('Failed downtime host on Icinga (likely already removed)')

    netbox_data = netbox.fetch_host_detail(hostname)
    is_virtual = netbox_data['is_virtual']
    if is_virtual:
        vm = ganeti.instance(fqdn, cluster=netbox_data['ganeti_cluster'])
        spicerack.actions[fqdn].success('Found Ganeti VM')
    else:
        ipmi = spicerack.ipmi(cached=True)
        mgmt = spicerack.management().get_fqdn(fqdn)
        spicerack.actions[fqdn].success('Found physical host')

    if is_virtual:
        try:
            vm.shutdown()
            spicerack.actions[fqdn].success('VM shutdown')
        except RemoteExecutionError as e:
            spicerack.actions[fqdn].failure('**Failed to shutdown VM, manually run gnt-instance remove on the Ganeti '
                                            'master for the {cluster} cluster**: {e}'.format(cluster=vm.cluster, e=e))

        try:
            # TODO: avoid race conditions to run it at the same time that the systemd timer will trigger it
            spicerack.netbox_master_host.run_sync(
                'systemctl start netbox_ganeti_{cluster}_sync.service'.format(cluster=vm.cluster.split('.')[2]))
            # TODO: add polling and validation that it completed to run
            spicerack.actions[fqdn].success(
                'Started forced sync of VMs in Ganeti cluster {cluster} to Netbox'.format(cluster=vm.cluster))
        except (DnsError, RemoteExecutionError) as e:
            spicerack.actions[fqdn].failure(
                '**Failed to force sync of VMs in Ganeti cluster {cluster} to Netbox**: {e}'.format(
                    cluster=vm.cluster, e=e))

    else:  # Physical host
        try:
            icinga.downtime_hosts([mgmt], reason)
            spicerack.actions[fqdn].success('Downtimed management interface on Icinga')
        except RemoteExecutionError:
            spicerack.actions[fqdn].failure('Skipped downtime management interface on Icinga (likely already removed)')

        try:
            remote_host.run_sync('true')
            can_connect = True
        except RemoteExecutionError as e:
            spicerack.actions[fqdn].failure(
                '**Unable to connect to the host, wipe of bootloaders will not be performed**: {e}'.format(e=e))
            can_connect = False

        if can_connect:
            try:
                # Call wipefs with globbing on all top level devices of type disk reported by lsblk
                remote_host.run_sync((r"lsblk --all --output 'NAME,TYPE' --paths | "
                                      r"awk '/^\/.* disk$/{ print $1 }' | "
                                      r"xargs -I % bash -c '/sbin/wipefs --all --force %*'"))
                spicerack.actions[fqdn].success('Wiped bootloaders')
            except RemoteExecutionError as e:
                spicerack.actions[fqdn].failure(('**Failed to wipe bootloaders, manual intervention required to make '
                                                 'it unbootable**: {e}').format(e=e))

        try:
            ipmi.command(mgmt, ['chassis', 'power', 'off'])
            spicerack.actions[fqdn].success('Powered off')
        except IpmiError as e:
            spicerack.actions[fqdn].failure('**Failed to power off, manual intervention required**: {e}'.format(e=e))

        update_netbox(netbox, netbox_data)
        spicerack.actions[fqdn].success('Set Netbox status to Decommissioning and deleted all non-mgmt interfaces '
                                        'and related IPs')

    logger.info('Sleeping for 20s to avoid race conditions...')
    time.sleep(20)

    debmonitor.host_delete(fqdn)
    spicerack.actions[fqdn].success('Removed from DebMonitor')

    puppet_master.delete(fqdn)
    spicerack.actions[fqdn].success('Removed from Puppet master and PuppetDB')

    if is_virtual:
        logger.info('Issuing Ganeti remove command, it can take up to 15 minutes...')
        try:
            vm.remove()
            spicerack.actions[fqdn].success('VM removed')
        except RemoteExecutionError as e:
            spicerack.actions[fqdn].failure('**Failed to remove VM, manually run gnt-instance remove on the Ganeti '
                                            'master for the {cluster} cluster**: {e}'.format(cluster=vm.cluster, e=e))

    dc = netbox.api.dcim.sites.get(netbox_data['site']).slug
    if dc in MIGRATED_PRIMARY_SITES:
        dns_netbox_args = dns_netbox_argparse().parse_args(
            ['{host} decommissioned, removing primary IPs'.format(host=hostname)])
        dns_netbox_run(dns_netbox_args, spicerack)
    else:
        spicerack.actions[fqdn].warning('**Site {dc} DNS records not yet migrated to the automatic system, manual '
                                        'patch required**'.format(dc=dc))


def update_netbox(netbox, netbox_data):
    """Delete all non-mgmt interfaces and set the status to Decommissioning.

    The deletion of the interface automatically deletes on cascade the related IPs and unset the primary IPs.
    """
    for interface in netbox.api.dcim.interfaces.filter(device_id=netbox_data['id']):
        if interface.mgmt_only:
            logger.debug('Skipping interface %s, mgmt_only=True', interface.name)
            continue
        logger.info('Deleting interface %s and related IPs', interface.name)
        interface.delete()
    netbox.put_host_status(netbox_data['name'], 'Decommissioning')


def get_grep_patterns(dns, decom_hosts):
    """Given a list of hostnames return the list of regex patterns for the hostname and all its IPs."""
    patterns = []
    for host in decom_hosts:
        patterns.append(re.escape(host))
        for ip in dns.resolve_ips(host):
            patterns.append(re.escape(ip))

    return patterns


def check_patterns_in_repo(host_paths, patterns):
    """Git grep for all the given patterns in the given hosts and paths and ask for confirmation if any is found.

    Arguments:
        host_paths (sequence): a sequence of 2-item tuples with the RemoteHost instance and the path of the
            repositories to check.
        patterns (sequence): a sequence of patterns to check.

    """
    grep_command = "git grep -E '({patterns})'".format(patterns='|'.join(patterns))
    ask = False
    for remote_host, path in host_paths:
        logger.info('Looking for matches in %s:%s', remote_host, path)
        command = 'cd {path} && {grep}'.format(path=path, grep=grep_command)
        for _, output in remote_host.run_sync(Command(command, ok_codes=[])):
            ask = True
            logger.info(output.message().decode())

    if ask:
        ask_confirmation('Found match(es) in the Puppet or mediawiki-config repositories (see above), proceed anyway?')
    else:
        logger.info('No matches found in the Puppet or mediawiki-config repositories')


def run(args, spicerack):
    """Required by Spicerack API."""
    has_failures = False
    remote = spicerack.remote()
    try:
        decom_hosts = remote.query(args.query).hosts
    except RemoteError:
        logger.debug("Query '%s' did not match any host or failed", args.query, exc_info=True)
        decom_hosts = NodeSet(args.query)
        ask_confirmation(('ATTENTION: the query does not match any host in PuppetDB or failed\n'
                          'Hostname expansion matches {n} hosts: {hosts}\n'
                          'Do you want to proceed anyway?').format(n=len(decom_hosts), hosts=decom_hosts))

    if len(decom_hosts) > 20:
        logger.error('Matched %d hosts, aborting. (max 20 with --force, 5 without)', len(decom_hosts))
        return 1
    elif len(decom_hosts) > 5:
        if args.force:
            logger.info('Authorized decommisioning of %s hosts with --force', len(decom_hosts))
        else:
            logger.error('Matched %d hosts, and --force not set aborting. (max 20 with --force, 5 without)',
                         len(decom_hosts))
            return 1

    ask_confirmation('ATTENTION: destructive action for {n} hosts: {hosts}\nAre you sure to proceed?'.format(
        n=len(decom_hosts), hosts=decom_hosts))

    # Check for references in the Puppet and mediawiki-config repositories.
    puppet_master = remote.query(get_puppet_ca_hostname())
    dns = spicerack.dns()
    deployment_host = remote.query(dns.resolve_cname(DEPLOYMENT_HOST))
    patterns = get_grep_patterns(dns, decom_hosts)
    # TODO: once all the host DNS records are automatically generated from Netbox check also the DNS repository.
    check_patterns_in_repo((
        (puppet_master, PUPPET_REPO_PATH),
        (puppet_master, PUPPET_PRIVATE_REPO_PATH),
        (deployment_host, MEDIAWIKI_CONFIG_REPO_PATH),
    ), patterns)

    reason = spicerack.admin_reason('Host decommission', task_id=args.task_id)
    phabricator = spicerack.phabricator(PHABRICATOR_BOT_CONFIG_FILE)

    for fqdn in decom_hosts:  # Doing one host at a time to track executed actions.
        try:
            _decommission_host(fqdn, spicerack, reason)
        except Exception as e:
            message = 'Host steps raised exception'
            logger.exception(message)
            spicerack.actions[fqdn].failure('{message}: {e}'.format(message=message, e=e))

        if spicerack.actions[fqdn].has_failures:
            has_failures = True

    suffix = ''
    if has_failures:
        suffix = '**ERROR**: some step on some host failed, check the bolded items above'
        logger.error('ERROR: some step failed, check the task updates.')

    message = '{name} executed by {owner} for hosts: `{hosts}`\n{actions}\n{suffix}'.format(
        name=__name__, owner=reason.owner, hosts=decom_hosts, actions=spicerack.actions, suffix=suffix)
    phabricator.task_comment(args.task_id, message)

    return int(has_failures)
