#!/usr/bin/env python3
"""Kubeadm deployment tool related code."""
from __future__ import annotations

import logging

import yaml
from spicerack.remote import Remote

from wmcs_libs.common import CuminParams, run_one_raw, simple_create_file
from wmcs_libs.k8s.kubernetes import KubernetesController, KubernetesTimeoutForNotReady

LOGGER = logging.getLogger(__name__)


PKI_FILES_TO_TRANSFER = [
    "ca.crt",
    "ca.key",
    "sa.key",
    "sa.pub",
    "front-proxy-ca.crt",
    "front-proxy-ca.key",
    "front-proxy-client.crt",
    "front-proxy-client.key",
]

KUBEADM_VERSION_COMPONENT_HIERA_KEY = "profile::wmcs::kubeadm::component"
KUBERNETES_VERSION_HIERA_KEY = "profile::wmcs::kubeadm::kubernetes_version"


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

    def __init__(self, remote: Remote, target_node_fqdn: str):
        """Init."""
        self._remote = remote
        self._target_node_fqdn = target_node_fqdn
        self._target_node = self._remote.query(f"D{{{self._target_node_fqdn}}}", use_sudo=True)

    def get_nodes_domain(self) -> str:
        """Get the network domain for the nodes in the cluster."""
        return self._target_node_fqdn.split(".", 1)[-1]

    def get_new_token(self) -> str:
        """Creates a new bootstrap token."""
        raw_output = run_one_raw(
            command=["kubeadm", "token", "create"], node=self._target_node, cumin_params=CuminParams(print_output=False)
        )
        output = raw_output.splitlines()[-1].strip()
        if not output:
            raise KubeadmCreateTokenError(f"Error creating a new token:\nOutput:{raw_output}")

        return output

    def delete_token(self, token: str) -> str:
        """Removes the given bootstrap token."""
        raw_output = run_one_raw(
            command=["kubeadm", "token", "delete", token],
            node=self._target_node,
            cumin_params=CuminParams(print_output=False),
        )
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
            node=self._target_node,
        )
        return raw_output.strip()

    def join(
        self,
        kubernetes_controller: KubernetesController,
        wait_for_ready: bool = True,
        timeout_seconds: int = 600,
        is_control: bool = False,
    ) -> None:
        """Join this node to the kubernetes cluster controlled by the given controller."""
        control_kubeadm = KubeadmController(
            remote=self._remote, target_node_fqdn=kubernetes_controller.controlling_node_fqdn
        )
        cluster_info = kubernetes_controller.get_cluster_info()
        # kubeadm does not want the protocol part https?://
        join_address = cluster_info.master_url.split("//", 1)[-1]
        ca_cert_hash = control_kubeadm.get_ca_cert_hash()
        new_token = control_kubeadm.get_new_token()

        command = [
            "kubeadm",
            "join",
            join_address,
            "--token",
            new_token,
            "--discovery-token-ca-cert-hash",
            f"sha256:{ca_cert_hash}",
        ]

        if is_control:
            command.append("--control-plane")

        try:
            run_one_raw(
                command=command,
                node=self._target_node,
            )

            if not wait_for_ready:
                return

            new_node_hostname = self._target_node_fqdn.split(".", 1)[0]
            try:
                kubernetes_controller.wait_for_ready(node_hostname=new_node_hostname, timeout_seconds=timeout_seconds)
            except KubernetesTimeoutForNotReady as e:
                raise KubeadmTimeoutForNodeReady(str(e)) from e

        finally:
            control_kubeadm.delete_token(token=new_token)

    def copy_certificates_from(self, existing_node_fqdn: str):
        """Copy certificate data from an existing control node to a new one."""
        existing_node = self._remote.query(f"D{{{existing_node_fqdn}}}", use_sudo=True)
        for file in PKI_FILES_TO_TRANSFER:
            file_full_path = f"/etc/kubernetes/pki/{file}"

            file_content = run_one_raw(
                command=["cat", file_full_path],
                node=existing_node,
                cumin_params=CuminParams(print_output=False, is_safe=True),
            )

            # TODO: this is a terrible heuristic but it works
            if file.endswith(".key"):
                # Create the file beforehand so it does not become world-writable
                run_one_raw(
                    command=["install", "-b", "-m", "0600", "/dev/null", file_full_path],
                    node=self._target_node,
                )

            simple_create_file(
                dst_node=self._target_node,
                contents=file_content,
                remote_path=file_full_path,
                use_root=True,
                cumin_params=CuminParams(print_output=False),
            )

    def get_etcd_nodes(self, existing_control_node_fqdn: str) -> list[str]:
        """Get list of etcd nodes currently known to kubeadm."""
        kubectl = KubernetesController(self._remote, existing_control_node_fqdn)
        kubeadm_config = kubectl.get_object("configmaps", "kubeadm-config", namespace="kube-system")
        config = yaml.safe_load(kubeadm_config["data"]["ClusterConfiguration"])

        return [endpoint.split("//", 1)[1].split(":")[0] for endpoint in config["etcd"]["external"]["endpoints"]]
