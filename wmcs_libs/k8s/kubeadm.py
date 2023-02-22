#!/usr/bin/env python3
"""Kubeadm deployment tool related code."""
from __future__ import annotations

import json
import logging
import time

from spicerack.remote import Remote

from wmcs_libs.common import run_one_raw
from wmcs_libs.k8s.kubernetes import KubernetesController

LOGGER = logging.getLogger(__name__)


class KubeadmError(Exception):
    """Parent class for all kubeadm related errors."""


class KubeadmDeleteTokenError(KubeadmError):
    """Raised when there was an error deleting a token."""


class KubeadmCreateTokenError(KubeadmError):
    """Raised when there was an error creating a token."""


class KubeadmTimeoutForNodeReady(KubeadmError):
    """Raised when a node did not get to Ready status on time."""


class KubeadmController:
    """Controller for a Kubeadmin managed kubernetes cluster."""

    def __init__(self, remote: Remote, controlling_node_fqdn: str):
        """Init."""
        self._remote = remote
        self._controlling_node_fqdn = controlling_node_fqdn
        self._controlling_node = self._remote.query(f"D{{{self._controlling_node_fqdn}}}", use_sudo=True)

    def get_nodes_domain(self) -> str:
        """Get the network domain for the nodes in the cluster."""
        return self._controlling_node_fqdn.split(".", 1)[-1]

    def get_new_token(self) -> str:
        """Creates a new bootstrap token."""
        raw_output = run_one_raw(command=["kubeadm", "token", "create"], node=self._controlling_node)
        output = raw_output.splitlines()[-1].strip()
        if not output:
            raise KubeadmCreateTokenError(f"Error creating a new token:\nOutput:{raw_output}")

        return output

    def delete_token(self, token: str) -> str:
        """Removes the given bootstrap token."""
        raw_output = run_one_raw(command=["kubeadm", "token", "delete", token], node=self._controlling_node)
        if "deleted" not in raw_output:
            raise KubeadmDeleteTokenError(f"Error deleting token {token}:\nOutput:{raw_output}")

        return raw_output.strip()

    def get_ca_cert_hash(self) -> str:
        """Retrieves the CA cert hash to use when bootstrapping."""
        raw_output = run_one_raw(
            command=[
                "openssl x509 -pubkey -in /etc/kubernetes/pki/ca.crt",
                "| openssl rsa -pubin -outform der 2>/dev/null",
                "| openssl dgst -sha256 -hex",
                "| sed 's/^.* //'",
            ],
            node=self._controlling_node,
        )
        return raw_output.strip()

    def join(
        self, kubernetes_controller: KubernetesController, wait_for_ready: bool = True, timeout_seconds: int = 600
    ) -> None:
        """Join this node to the kubernetes cluster controlled by the given controller."""
        control_kubeadm = KubeadmController(
            remote=self._remote, controlling_node_fqdn=kubernetes_controller.controlling_node_fqdn
        )
        cluster_info = kubernetes_controller.get_cluster_info()
        # kubeadm does not want the protocol part https?://
        join_address = cluster_info.master_url.split("//", 1)[-1]
        ca_cert_hash = control_kubeadm.get_ca_cert_hash()
        new_token = control_kubeadm.get_new_token()
        try:
            run_one_raw(
                command=[
                    "kubeadm",
                    "join",
                    join_address,
                    "--token",
                    new_token,
                    "--discovery-token-ca-cert-hash",
                    f"sha256:{ca_cert_hash}",
                ],
                node=self._controlling_node,
            )

            if not wait_for_ready:
                return

            new_node_hostname = self._controlling_node_fqdn.split(".", 1)[0]
            check_interval_seconds = 10
            start_time = time.time()
            cur_time = start_time
            while cur_time - start_time < timeout_seconds:
                if kubernetes_controller.is_node_ready(node_hostname=new_node_hostname):
                    return

                time.sleep(check_interval_seconds)
                cur_time = time.time()

            cur_conditions = kubernetes_controller.get_node(node_hostname=new_node_hostname)[0]["conditions"]
            raise KubeadmTimeoutForNodeReady(
                f"Waited {timeout_seconds} for the node {new_node_hostname} to "
                "become healthy, but it never did. Current conditions:\n"
                f"{json.dumps(cur_conditions, indent=4)}"
            )

        finally:
            control_kubeadm.delete_token(token=new_token)
