"""Network Cookbooks"""
import json
import logging
from typing import Dict, List

from spicerack.netbox import Netbox
from spicerack.remote import RemoteHosts
from wmflib.interactive import ask_confirmation

__title__ = __doc__
logger = logging.getLogger(__name__)


def configure_switch_interfaces(remote: RemoteHosts, netbox: Netbox, netbox_data: Dict,
                                print_output: bool = False) -> None:
    """Configure the switch interfaces relevant to a device.

    Arguments:
        remote: Spicerack remote instance
        netbox: pynetbox instance on the selected Netbox server
        netbox_data: Dict of netbox fields about the target device
        print_output: Display a more verbose output

    """
    # Get all the device's main interfaces (production, connected)
    # Most servers have 1 uplink, some have 2, a few have more
    nb_device_interfaces = netbox.api.dcim.interfaces.filter(
        device_id=netbox_data['id'], mgmt_only=False, connected=True)
    # Tackle interfaces one at a time
    for nb_device_interface in nb_device_interfaces:

        nb_switch_interface = nb_device_interface.connected_endpoint
        # Get the switch FQDN (VC or not VC)
        vc = nb_switch_interface.device.virtual_chassis
        if vc:
            switch_fqdn = vc.name
        else:
            switch_fqdn = nb_switch_interface.device.primary_ip.dns_name
        logger.debug("%s is connected to %s:%s", nb_device_interface, switch_fqdn, nb_switch_interface)

        remote_host = remote.query('D{' + switch_fqdn + '}')
        # Get the live interface config adds them in alphabetic order in a Netbox like dict,
        # or None if it doesn't exist at all
        live_interface = get_junos_live_interface_config(remote_host, nb_switch_interface.name, print_output)

        commands = junos_set_interface_config(netbox_data, live_interface, nb_switch_interface)

        if commands:
            run_junos_commands(remote_host, commands)
        else:
            logger.info("No configuration change needed on the switch for %s", nb_device_interface)


def junos_set_interface_config(netbox_data: Dict, live_interface: Dict,  # pylint: disable=too-many-branches
                               nb_switch_interface) -> List[str]:
    """Return a list of Junos set commands needed to configure the interface

    Arguments:
        netbox_data: Dict of netbox fields about the target device
        live_interface: running configuration of a given Junos interface in a Netbox format
        nb_switch_interface: Instance of relevant Netbox interface

    """
    commands = []
    device_name = netbox_data['name']
    # We want to disable the interface if it's disabled in Netbox
    if not nb_switch_interface.enabled:
        # If there is already something configured and the interface is enabled: clear it
        if live_interface and not live_interface['enabled']:
            # But first a safeguard
            if device_name not in live_interface['description']:
                logger.error("Need to disable %s:%s, "
                             "but the switch interface description doesn't match the server name:\n"
                             "%s vs %s",
                             nb_switch_interface.device,
                             nb_switch_interface,
                             device_name,
                             live_interface['description'])
                return commands
            # Delete the interface to clear all its properties
            commands.append(f"delete interfaces {nb_switch_interface}")

        commands.extend([f'set interfaces {nb_switch_interface} description "DISABLED {device_name}"',
                         f"set interfaces {nb_switch_interface} disabled",
                         ])

    else:  # the interface is enabled in Netbox
        # The interface doesn't exists yet on the device, configure it
        # Status
        if live_interface and not live_interface['enabled']:
            commands.append(f'delete interfaces {nb_switch_interface} disable')
        # Description
        description = device_name
        # Safeguard for accidental " that would be interpreted as an end of comment by Junos
        cable_label = nb_switch_interface.cable.label
        if cable_label and '"' not in cable_label:
            description += f" {{#{cable_label}}}"
        if not live_interface or live_interface['description'] != description:
            commands.append(f'set interfaces {nb_switch_interface} description "{description}"')

        # MTU
        if nb_switch_interface.mtu and (not live_interface or live_interface['mtu'] != nb_switch_interface.mtu):
            commands.append(f'set interfaces {nb_switch_interface} mtu {nb_switch_interface.mtu}')

        # VLAN
        if nb_switch_interface.mode:
            # Interface mode
            if not live_interface or live_interface['mode'] != nb_switch_interface.mode.value:
                # Junos call it trunk, Netbox tagged
                junos_mode = 'access' if nb_switch_interface.mode.value == 'access' else 'trunk'
                commands.append(f'set interfaces {nb_switch_interface} unit 0 family '
                                f'ethernet-switching interface-mode {junos_mode}')

            vlans_members = []
            # Native vlan
            if nb_switch_interface.mode.value == 'tagged' and nb_switch_interface.untagged_vlan:
                if (not live_interface or
                   live_interface['native-vlan-id'] != nb_switch_interface.untagged_vlan.vid):
                    commands.append(f'set interfaces {nb_switch_interface} '
                                    f'native-vlan-id {nb_switch_interface.untagged_vlan.vid}')
            if nb_switch_interface.untagged_vlan:
                vlans_members.append(nb_switch_interface.untagged_vlan.name)
            for tagged_vlan in nb_switch_interface.tagged_vlans or []:
                vlans_members.append(tagged_vlan.name)
            if not live_interface or live_interface['vlans'] != sorted(vlans_members):
                if len(vlans_members) == 0:
                    logger.error("No vlans configured for %s:%s",
                                 nb_switch_interface.device,
                                 nb_switch_interface)
                else:
                    # Delete the configured vlans, and re-add the needed ones
                    commands.append(f'delete interfaces {nb_switch_interface} unit 0 family '
                                    f'ethernet-switching vlan members')
                    commands.append(f'set interfaces {nb_switch_interface} unit 0 family '
                                    f"ethernet-switching vlan members [ {' '.join(sorted(vlans_members))} ]")

    return commands


def run_junos_commands(remote_host: RemoteHosts, conf_commands: List) -> None:
    """Run commands on Juniper devices, first load the commands show the diff then exit.

       Then commit confirm it.
       Then commit check.

    Arguments:
        remote_host: Spicerack RemoteHosts instance
        conf_commands: list of Junos set commands to run

    """
    # Once we get more trust in the system, we could commit the change without prompting the user.
    for mode in ['compare', 'commit', 'confirm']:
        is_safe = False
        commands = ['configure exclusive']  # Enter configuration mode with a lock on the config
        commands.extend(conf_commands)  # Add the actions

        if mode == 'compare':
            commands.extend(['show|compare',  # Get a diff
                             'rollback',  # Leave no trace
                             'exit'])  # Cleanly close
            is_safe = True
        elif mode == 'commit':
            commands.extend(['show|compare',  # Get a diff
                             'commit confirmed 1',  # Auto rollback if any issues
                             'exit'])  # Cleanly close
        elif mode == 'confirm':
            commands = ['configure',
                        'commit check',
                        'exit']

        remote_host.run_sync(';'.join(commands), is_safe=is_safe, print_progress_bars=False)
        if mode == 'compare':
            ask_confirmation('Commit the above change?')
        elif mode == 'commit':
            logger.info('Commited the above change, needs to be confirmed')
        elif mode == 'confirm':
            logger.info('Change confirmed')


def parse_results(results_raw, json_output=False):
    """Parse a single device cumin output."""
    # Only supports 1 target device at a time
    results = RemoteHosts.results_to_list(results_raw)
    if not results:  # In dry run, run_sync/async will return an empty dict
        return None
    result = results[0][1]
    # If empty result (eg. interface not configured)
    if not result:
        return None
    if json_output:
        return json.loads(result)
    return result


def junos_interface_to_netbox(config: str, old_junos: bool) -> Dict:
    """Converts a Junos JSON interface config to a dict similar to Netbox interfaces.

    Arguments:
        config: interface json config
        old_junos: if the JSON returned is ancient

    """
    interface = {
        'name': config['name']['data'] if old_junos else config['name'],
        'enabled': 'disable' not in config,
        'description': config.get('description', [{}])[0].get('data') if old_junos else config.get('description'),
        'mode': None,
        'vlans': None
    }
    if old_junos:
        for key in ('mtu', 'native-vlan-id'):
            interface[key] = int(config.get(key, [{}])[0].get('data', 0)) or None
    else:
        for key in ('mtu', 'native-vlan-id'):
            interface[key] = config.get(key)
    # vlans
    try:
        if old_junos:
            eth_sw = config['unit'][0]['family'][0]['ethernet-switching'][0]
        else:
            eth_sw = config['unit'][0]['family']['ethernet-switching']
    except (IndexError, KeyError):
        logger.debug('No ethernet switching configured.')
        return interface

    if 'interface-mode' in eth_sw:
        # Junos call it access and trunk
        # Nebox use a dict with label (eg. Access) and value (eg. access), keeping it simpler here
        interface_mode = eth_sw['interface-mode'][0]['data'] if old_junos else eth_sw['interface-mode']
        interface['mode'] = 'access' if interface_mode == 'access' else 'tagged'

    # get a usable set of configured vlans
    vlans = []
    if 'vlan' in eth_sw:
        if old_junos:
            for vlan_raw in eth_sw['vlan'][0]['members']:
                vlans.append(vlan_raw['data'])
        else:
            vlans = eth_sw['vlan']['members']

    interface['vlans'] = sorted(vlans)

    return interface


def get_junos_live_interface_config(remote_host: RemoteHosts, interface: str, print_output: bool = False) -> Dict:
    """Returns the running configuration of a given Junos interface in a Netbox format.

    Arguments:
        remote_host: Spicerack RemoteHosts instance
        interface: target interface name
        print_output: Display a more verbose output

    """
    # Get the interface config
    logger.debug("Fetching the live interface config")
    results_raw = remote_host.run_sync(f"show configuration interfaces {interface} | display json",
                                       is_safe=True,
                                       print_output=print_output,
                                       print_progress_bars=False)
    try:
        result_json = parse_results(results_raw, json_output=True)
        if isinstance(result_json['configuration'], list):
            old_junos = True
            interface_json = result_json['configuration'][0]['interfaces'][0]['interface'][0]
        elif isinstance(result_json['configuration'], dict):
            old_junos = False
            interface_json = result_json['configuration']['interfaces']['interface'][0]
        else:
            logger.error('Network device returned unknown data: "%s"', result_json)
            return None
    except (KeyError, TypeError) as e:
        logger.error('Network device returned invalid data: "%s". Error: %s', results_raw, e)
        return None
    return junos_interface_to_netbox(interface_json, old_junos)
