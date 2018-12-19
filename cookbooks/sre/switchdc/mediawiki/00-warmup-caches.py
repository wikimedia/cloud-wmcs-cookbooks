"""Warmup MediaWiki caches"""
import logging

from spicerack.interactive import ask_confirmation

from cookbooks.sre.switchdc.mediawiki import argument_parser_base, post_process_args


__title__ = __doc__
logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


def argument_parser():
    """As specified by Spicerack API."""
    return argument_parser_base(__name__, __title__)


def run(args, spicerack):
    """Required by Spicerack API."""
    post_process_args(args)
    if args.live_test:
        logger.info('Inverting DC to perform the warmup in %s (passive DC)', args.dc_from)
        datacenter = args.dc_from
    else:
        datacenter = args.dc_to

    ask_confirmation('Are you sure to warmup caches in {dc}?'.format(dc=datacenter))
    logger.info('Running warmup script in %s', datacenter)

    warmup_dir = '/var/lib/mediawiki-cache-warmup'
    memc_warmup = "nodejs {dir}/warmup.js {dir}/urls-cluster.txt spread appservers.svc.{dc}.wmnet".format(
        dir=warmup_dir, dc=datacenter)
    appserver_warmup = "nodejs {dir}/warmup.js {dir}/urls-server.txt clone appserver {dc}".format(
        dir=warmup_dir, dc=datacenter)

    maintenance_host = spicerack.mediawiki().get_maintenance_host(datacenter)
    maintenance_host.run_sync(memc_warmup, appserver_warmup)
