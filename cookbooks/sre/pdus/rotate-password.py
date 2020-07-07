"""Update Sentry PDUs passwords.

- Optionally checks if the default user is still configured.
- Default user is 'root'
- If host 'all' is passed, will iterate over all PDUs
- So try --dry-run first 😉

Usage example:
    cookbook sre.pdus.rotate-pdu-password --username MrFoo 'ps1-b5-eqiad.mgmt.eqiad.wmnet'
    cookbook sre.pdus.rotate-pdu-password all
    cookbook sre.pdus.rotate-pdu-password all --check_default
"""

import logging

from requests import Session
from requests.exceptions import HTTPError

from spicerack.interactive import ensure_shell_is_durable, get_secret

from cookbooks.sre import pdus


__title__ = 'Update Sentry PDUs 🔌 passwords'
logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class PasswordResetError(Exception):
    """Raised if password reset fails"""


def argument_parser():
    """As specified by Spicerack API."""
    return pdus.argument_parser_base()


def change_password(pdu, session, new_password):
    """Change the password

    Arguments:
        pdu (str): the pdu fqdn
        session (requests.Session): A configured request session
        new_password (str): the new password to use

    Raises:
        PasswordResetError

    """
    payload = {
        3: {
            'Current_Password': session.auth[1],
            'New_Password': new_password,
            'New_Password_Verify': new_password
        },
        4: {
            'FormButton': 'Apply',
            'UPWC': session.auth[1],
            'UPW': new_password,
            'UPWV': new_password
        }
    }.get(pdus.get_version(pdu, session))

    # Then change the password
    try:
        response = session.post("https://{}/Forms/chngpswd_1".format(pdu), data=payload)
        response.raise_for_status()
    except HTTPError as err:
        raise PasswordResetError("{}: Error {} while trying to change the password: {}".format(
            pdu, response.status_code, err))

    session.auth[1] = new_password
    session.cookies.clear()
    try:
        response = session.get("https://{}/chngpswd.html".format(pdu))
        response.raise_for_status()
    except HTTPError as err:
        raise PasswordResetError(
            '{}: Error {}. New password not working, the change probably failed:\n{}'.format(
                pdu, response.status_code, err))
    logger.info('%s: Password updated successfully 😌', pdu)


def run(args, spicerack):
    """Required by Spicerack API."""
    ensure_shell_is_durable()
    session = Session()
    session.verify = False
    return_code = 0
    current_password = get_secret('Current password')
    new_password = get_secret("New password", confirm=True)

    session.auth = (args.username, current_password)

    # TODO: check if self.query is a PDU in netbox
    _pdus = pdus.get_pdu_ips(spicerack.netbox()) if args.query == 'all' else set([args.query])

    for pdu in _pdus:
        try:
            if not spicerack.dry_run:
                change_password(pdu, session, new_password)
            else:
                logger.info('%s: Dry run, not trying.', pdu)
            if args.check_default:
                if pdus.check_default(pdu):
                    # TODO: delete default user
                    return_code = 1
        except (pdus.VersionError, PasswordResetError) as error:
            logger.error(error)
            return_code = 1
    return return_code