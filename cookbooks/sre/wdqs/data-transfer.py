"""WDQS data transfer cookbook for source node

Usage example for hosts behind lvs:
    cookbook sre.wdqs.data-transfer --source wdqs1004.eqiad.wmnet --dest wdqs1003.eqiad.wmnet
     --reason "allocator troubles" --blazegraph_instance wikidata --task-id T12345

Usage example for test hosts:
    cookbook sre.wdqs.data-transfer --source wdqs1009.eqiad.wmnet --dest wdqs1010.eqiad.wmnet
     --reason "moving away from legacy updater" --without-lvs --blazegraph_instance wikidata --task-id T12345

"""
import argparse
import logging
import string
import threading
from contextlib import contextmanager

from datetime import timedelta
from random import SystemRandom
from textwrap import dedent
from time import sleep

from spicerack.kafka import ConsumerDefinition
from spicerack.remote import RemoteExecutionError

from cookbooks.sre.wdqs import check_hosts_are_valid, wait_for_updater, get_site, get_hostname, MUTATION_TOPICS

BLAZEGRAPH_INSTANCES = {
    'categories': {
        'services': ['wdqs-categories'],
        'data_path': '/srv/wdqs',
        'files': ['/srv/wdqs/categories.jnl', '/srv/wdqs/aliases.map'],
        'valid_on': 'wdqs',
    },
    'wikidata': {
        'services': ['wdqs-updater', 'wdqs-blazegraph'],
        'data_path': '/srv/wdqs',
        'files': ['/srv/wdqs/wikidata.jnl'],
        'valid_on': 'wdqs',
    },
    'commons': {
        'services': ['wcqs-updater', 'wcqs-blazegraph'],
        'data_path': '/srv/query_service',
        'files': ['/srv/query_service/wcqs.jnl'],
        'valid_on': 'wcqs',
    },
}

__title__ = "WDQS data transfer cookbook"
logger = logging.getLogger(__name__)


def argument_parser():
    """Parse the command line arguments for all the sre.elasticsearch cookbooks."""
    parser = argparse.ArgumentParser(prog=__name__, description=__doc__,
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--source', required=True, help='FQDN of source node.')
    parser.add_argument('--dest', required=True, help='FQDN of destination node.')
    parser.add_argument('--blazegraph_instance', required=True, choices=list(BLAZEGRAPH_INSTANCES.keys()),
                        help='One of: %(choices)s.')
    parser.add_argument('--reason', required=True, help='Administrative Reason')
    parser.add_argument('--downtime', type=int, default=6, help="Hours of downtime")
    parser.add_argument('--task-id', help='task_id for the change')
    parser.add_argument('--without-lvs', action='store_false', dest='with_lvs', help='This cluster does not use LVS.')

    return parser


@contextmanager
def open_port(host, source, port, proto='tcp'):
    """Context manager that opens a firewall port.

    Arguments:
        host (str): the host on which to open the port
        source (str): the source from which to allow traffic
        port (int): the port to open
        proto (str): the proto (tcp/udp) to allow (default: tcp)

    """
    ferm_path = '/etc/ferm/conf.d/10_cookbooks.sre.wdqs.data-transfer'
    ferm_rule = """# Autogenerated by cookbook sre.wdqs.data-transfer
    &R_SERVICE([{proto}], {port}, (@resolve(({source})) @resolve(({source}),AAAA)));
    """.format(port=port, source=source, proto=proto)
    try:
        host.run_sync('echo "{}" > {}'.format(dedent(ferm_rule), ferm_path))
        host.run_sync('/bin/systemctl restart ferm')
        yield
    finally:
        host.run_sync('/bin/rm -fv {}'.format(ferm_path))
        host.run_sync('/bin/systemctl restart ferm')


def _copy_file(source, dest, file):
    """Copy file from one node to the other via netcat."""
    passwd = _generate_pass()
    port = 9876

    def receive(file, port, passwd):
        try:
            recv_cmd = "nc -l -p {port} | openssl enc -d -aes-256-cbc -k {passwd} | pigz -c -d > {file}".format(
                port=port, file=file, passwd=passwd)
            logger.info('Starting receiver on [%s] with [%s]', dest, recv_cmd)
            dest.run_sync(recv_cmd)
            logger.info('receiving file [%s] completed', file)
        except RemoteExecutionError:
            logger.error('Error when receiving file [%s].', file)

    def send(file, port, passwd):
        send_cmd = "pigz -c {file} | openssl enc -e -aes-256-cbc -k {passwd} | nc -w 3 {dest} {port}".format(
            file=file, dest=dest.hosts, passwd=passwd, port=port)
        logger.info('Starting to send file from [%s] with [%s]', source, send_cmd)
        source.run_sync(send_cmd)
        logger.info('sending file [%s] completed', file)

    receiver = threading.Thread(target=receive, args=(file, port, passwd))

    with open_port(dest, source, port):
        receiver.start()
        # sleep 10 seconds to ensure the receiver has started
        sleep(10)
        send(file, port, passwd)
        receiver.join()


def _generate_pass():
    """Generate a random string of fixed length."""
    sysrand = SystemRandom()
    passwd_charset = string.ascii_letters + string.digits
    return ''.join([sysrand.choice(passwd_charset) for _ in range(32)])


# pylint:disable=too-many-locals
def run(args, spicerack):
    """Required by Spicerack API."""
    remote = spicerack.remote()
    remote_hosts = remote.query("{source},{dest}".format(source=args.source, dest=args.dest))
    host_kind = check_hosts_are_valid(remote_hosts, remote)

    icinga_hosts = spicerack.icinga_hosts(remote_hosts.hosts)
    puppet = spicerack.puppet(remote_hosts)
    prometheus = spicerack.prometheus()
    reason = spicerack.admin_reason(args.reason, task_id=args.task_id)

    source = remote.query(args.source)
    dest = remote.query(args.dest)

    if len(source) != 1:
        raise ValueError("Only one node is needed. Not {total}({source})".
                         format(total=len(source), source=source))

    if len(dest) != 1:
        raise ValueError("Only one destination node is needed. Not {total}({source})".
                         format(total=len(source), source=source))

    instance = BLAZEGRAPH_INSTANCES[args.blazegraph_instance]
    if host_kind != instance['valid_on']:
        raise ValueError('Instance (valid_on:{}) is not valid for selected hosts ({})'.format(
            instance['valid_on'], host_kind))

    services = instance['services']
    files = instance['files']

    stop_services_cmd = " && ".join(["systemctl stop " + service for service in services])
    services.reverse()
    start_services_cmd = " && sleep 10 && ".join(["systemctl start " + service for service in services])

    with icinga_hosts.downtimed(reason, duration=timedelta(hours=args.downtime)):
        with puppet.disabled(reason):
            if args.with_lvs:
                logger.info('depooling %s', remote_hosts)
                remote_hosts.run_sync('depool')
                sleep(180)

            logger.info('Stopping services [%s]', stop_services_cmd)
            remote_hosts.run_sync(stop_services_cmd)

            for file in files:
                _copy_file(source, dest, file)
                dest.run_sync('chown blazegraph: "{file}"'.format(file=file))

            if args.blazegraph_instance in ('wikidata', 'commons'):
                logger.info('Touching "data_loaded" file to show that data load is completed.')
                dest.run_sync('touch {data_path}/data_loaded'.format(
                    data_path=instance['data_path']))

            if args.blazegraph_instance == 'categories':
                logger.info('Reloading nginx to load new categories mapping.')
                dest.run_sync('systemctl reload nginx')

            source_hostname = get_hostname(args.source)
            dest_hostname = get_hostname(args.dest)
            if args.blazegraph_instance in MUTATION_TOPICS:
                logger.info('Transferring Kafka offsets')
                kafka = spicerack.kafka()
                kafka.transfer_consumer_position([MUTATION_TOPICS[args.blazegraph_instance]],
                                                 ConsumerDefinition(get_site(source_hostname, spicerack), 'main',
                                                                    source_hostname),
                                                 ConsumerDefinition(get_site(dest_hostname, spicerack), 'main',
                                                                    dest_hostname))

            logger.info('Starting services [%s]', start_services_cmd)
            remote_hosts.run_sync(start_services_cmd)

            if args.blazegraph_instance in MUTATION_TOPICS:
                wait_for_updater(prometheus, get_site(source_hostname, spicerack), source)
                wait_for_updater(prometheus, get_site(dest_hostname, spicerack), dest)

            if args.with_lvs:
                logger.info('pooling %s', remote_hosts)
                remote_hosts.run_sync('pool')
