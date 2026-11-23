"""Network policy for the private briefing preview API."""

from __future__ import annotations

from ipaddress import ip_address, ip_network, IPv6Address
import json
from pathlib import Path
from urllib.parse import urlsplit


CONFIG_PATH = Path(__file__).with_name('api-access.json')
TAILSCALE_NETWORKS = (ip_network('100.64.0.0/10'),)
RFC1918_NETWORKS = (ip_network('10.0.0.0/8'), ip_network('172.16.0.0/12'),
                    ip_network('192.168.0.0/16'))


def _normalized_address(value: str):
    try:
        address = ip_address(str(value).split('%', 1)[0])
    except ValueError:
        return None
    if isinstance(address, IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def allowed_networks(config_path=CONFIG_PATH):
    """Always allow loopback and Tailscale; add only configured RFC1918 LANs."""
    networks = list(TAILSCALE_NETWORKS)
    try:
        config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return tuple(networks)
    rows = config.get('additional_lan_cidrs', []) if isinstance(config, dict) else []
    if not isinstance(rows, list):
        return tuple(networks)
    for row in rows:
        if not isinstance(row, str):
            continue
        try:
            network = ip_network(row, strict=False)
        except ValueError:
            continue
        if (network.version == 4 and any(network.subnet_of(private) for private in RFC1918_NETWORKS)
                and network not in networks):
            networks.append(network)
    return tuple(networks)


def is_loopback_address(value: str) -> bool:
    address = _normalized_address(value)
    return bool(address and address.is_loopback)


def is_loopback_only_request(method: str, path: str) -> bool:
    method = method.upper()
    path = urlsplit(path).path
    if method == 'DELETE' or path == '/api/profile/delete' or path.endswith('/delete'):
        return True
    if path in {'/api/usage', '/api/flags'}:
        return True
    if path == '/api/profile/export':
        return True
    return method == 'POST' and path == '/api/corrections'


def is_api_request_allowed(method: str, path: str, address: str, config_path=CONFIG_PATH) -> bool:
    if not urlsplit(path).path.startswith('/api/'):
        return True
    if is_loopback_only_request(method, path):
        return is_loopback_address(address)
    client = _normalized_address(address)
    if client is None:
        return False
    return client.is_loopback or any(client.version == network.version and client in network
                                     for network in allowed_networks(config_path))


def trusted_probe_address(addresses, config_path=CONFIG_PATH):
    """Choose an interface address allowed by the API policy for release smoke probes."""
    normalized = []
    for value in addresses:
        address = _normalized_address(value)
        if not address or address.is_loopback or address.is_link_local or address.is_unspecified:
            continue
        if is_api_request_allowed('GET', '/api/status', str(address), config_path):
            normalized.append(address)
    normalized.sort(key=lambda item: (0 if item in TAILSCALE_NETWORKS[0] else 1,
                                      item.version, int(item)))
    return str(normalized[0]) if normalized else None
