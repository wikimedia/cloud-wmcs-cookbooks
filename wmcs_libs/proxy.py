#!/usr/bin/env python3
"""Functions to setup a socks proxy"""
from __future__ import annotations

import base64
import logging
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import requests
from spicerack import Spicerack
from wmflib.config import load_yaml_config

BASE64_PUPPET_CA_URL = (
    "https://gerrit.wikimedia.org/r/plugins/gitiles/operations/puppet/"
    "+/refs/heads/production"
    "/modules/profile/files/pki/ROOT/Wikimedia_Internal_Root_CA.pem"
    "?format=TEXT"
)
LOGGER = logging.getLogger(__name__)
DEFAULT_PROXY_VIA_HOST = "cloudcumin1001.eqiad.wmnet"


def _is_proxy_working(port: int) -> bool:
    try:
        requests.get(
            "http://alertmanager-eqiad.wikimedia.org",
            proxies=dict(
                http=f"socks5h://127.0.0.1:{port}",
                https=f"socks5h://127.0.0.1:{port}",
            ),
            timeout=5,
        )
    except (requests.ConnectTimeout, requests.ConnectionError):
        return False

    if os.environ.get("http_proxy") == os.environ.get("https_proxy") == f"socks5h://127.0.0.1:{port}":
        return True

    return False


def _start_proxy(puppet_ca_path: Path, host: str, port: int) -> None:
    if _is_proxy_working(port=port):
        _stop_proxy(host=host, port=port)

    if "http_proxy" in os.environ:
        del os.environ["http_proxy"]
    if "https_proxy" in os.environ:
        del os.environ["https_proxy"]
    if "REQUESTS_CA_BUNDLE" in os.environ:
        del os.environ["REQUESTS_CA_BUNDLE"]

    subprocess.run(
        [
            "/usr/bin/ssh",
            # Do not run any command
            "-N",
            # Drop to the background
            "-f",
            # Start a socks proxy
            "-D",
            f"127.0.0.1:{port}",
            host,
        ],
        check=True,
    )
    os.environ["http_proxy"] = f"socks5h://127.0.0.1:{port}"
    os.environ["https_proxy"] = f"socks5h://127.0.0.1:{port}"
    os.environ["REQUESTS_CA_BUNDLE"] = str(puppet_ca_path.resolve().absolute())


def _stop_proxy(host: str, port: int) -> None:
    subprocess.run(["/usr/bin/pkill", "-f", f"D 127.0.0.1:{port}.*{host}"], check=True)
    if "http_proxy" in os.environ:
        del os.environ["http_proxy"]
    if "https_proxy" in os.environ:
        del os.environ["https_proxy"]
    if "REQUESTS_CA_BUNDLE" in os.environ:
        del os.environ["REQUESTS_CA_BUNDLE"]


def _download_puppet_ca(puppet_ca_path: Path):
    if not puppet_ca_path.exists():
        response = requests.get(BASE64_PUPPET_CA_URL, timeout=10)
        response.raise_for_status()
        raw_puppet_ca = base64.b64decode(response.text)
        puppet_ca_path.write_bytes(raw_puppet_ca)


@contextmanager
def with_proxy(spicerack: Spicerack):
    """Context manager that makes sure to start and tear down a socks proxy if needed.

    Used to be able to access internal apis when running from your laptop/remotely.
    """
    proxy_config_path = spicerack.config_dir / "wmcs.yaml"
    if not proxy_config_path.exists():
        LOGGER.debug("Skipping proxy start, no config found on %s.", str(proxy_config_path))
        yield
        return

    config = load_yaml_config(config_file=spicerack.config_dir / "wmcs.yaml", raises=False)
    LOGGER.info("Loading socks proxy config from %s", spicerack.config_dir / "wmcs.yaml")
    proxy_via_host = config.get("socks_proxy_host", DEFAULT_PROXY_VIA_HOST)
    socks_proxy_port = int(config.get("socks_proxy_port", "54123"))
    puppet_ca_path = (
        Path(config.get("puppet_ca_path", spicerack.config_dir / "puppet_ca.crt")).expanduser().resolve().absolute()
    )
    proxy_started = False
    if not _is_proxy_working(port=socks_proxy_port):
        try:
            LOGGER.info("Starting socks proxy on 127.0.0.1:%d", socks_proxy_port)
            _download_puppet_ca(puppet_ca_path=puppet_ca_path)
            _start_proxy(host=proxy_via_host, port=socks_proxy_port, puppet_ca_path=puppet_ca_path)
            proxy_started = True
        except Exception as error:  # pylint: disable=broad-except
            LOGGER.warning(
                "Unable to start the socks proxy, trying to run the cookbook without it... exception:%s", str(error)
            )
    else:
        LOGGER.info(
            "Proxy already running."
            if os.environ.get("https_proxy", None) is not None
            else "We already have access without proxy, skipping..."
        )
    try:
        yield
    finally:
        if proxy_started:
            LOGGER.info("Stopping proxy on 127.0.0.1:%d", socks_proxy_port)
            _stop_proxy(host=proxy_via_host, port=socks_proxy_port)
        else:
            LOGGER.info("The proxy was not started, not stopping.")
