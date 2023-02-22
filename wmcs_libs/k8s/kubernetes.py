#!/usr/bin/env python3
"""Generic kubernetes managing code."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from spicerack.remote import Remote

from wmcs_libs.common import run_one_as_dict, run_one_raw

LOGGER = logging.getLogger(__name__)


class KubernetesError(Exception):
    """Parent class for all kubernetes related errors."""


class KubernetesMalformedClusterInfo(KubernetesError):
    """Risen when the gotten cluster info is not formatted as expected."""


class KubernetesNodeNotFound(KubernetesError):
    """Risen when the given node does not exist."""


class KubernetesNodeStatusError(KubernetesError):
    """Risen when the given node status is not recognized."""


@dataclass(frozen=True)
class KubernetesClusterInfo:
    """Kubernetes cluster info."""

    master_url: str
    dns_url: str
    metrics_url: str

    @classmethod
    def form_cluster_info_output(cls, raw_output: str) -> "KubernetesClusterInfo":
        """Create the object from the cli 'kubectl cluster-info' output.

        Example of output:
        ```
        Kubernetes control plane is running at https://k8s.toolsbeta.eqiad1.wikimedia.cloud:6443  # noqa: E501
        CoreDNS is running at https://k8s.toolsbeta.eqiad1.wikimedia.cloud:6443/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy  # noqa: E501
        Metrics-server is running at https://k8s.toolsbeta.eqiad1.wikimedia.cloud:6443/api/v1/namespaces/kube-system/services/https:metrics-server:/proxy  # noqa: E501

        To further debug and diagnose cluster problems, use 'kubectl cluster-info dump'.
        ```
        """
        master_url = None
        dns_url = None
        metrics_url = None
        for line in raw_output.splitlines():
            # get rid of the terminal colors
            line = line.replace("\x1b[0;33m", "").replace("\x1b[0;32m", "").replace("\x1b[0m", "")
            if line.startswith("Kubernetes control plane"):
                master_url = line.rsplit(" ", 1)[-1]
            elif line.startswith("CoreDNS"):
                dns_url = line.rsplit(" ", 1)[-1]
            elif line.startswith("Metrics-server"):
                metrics_url = line.rsplit(" ", 1)[-1]

        if master_url is None or dns_url is None or metrics_url is None:
            raise KubernetesMalformedClusterInfo(f"Unable to parse cluster info:\n{raw_output}")

        return cls(master_url=master_url, dns_url=dns_url, metrics_url=metrics_url)


class KubernetesController:
    """Controller for a kubernetes cluster."""

    def __init__(self, remote: Remote, controlling_node_fqdn: str):
        """Init."""
        self._remote = remote
        self.controlling_node_fqdn = controlling_node_fqdn
        self._controlling_node = self._remote.query(f"D{{{self.controlling_node_fqdn}}}", use_sudo=True)

    def get_nodes_domain(self) -> str:
        """Get the network domain for the nodes in the cluster."""
        return self.controlling_node_fqdn.split(".", 1)[-1]

    def get_cluster_info(self) -> KubernetesClusterInfo:
        """Get cluster info."""
        raw_output = run_one_raw(
            # cluster-info does not support json output format (there's a dump
            # command, but it's too verbose)
            command=["kubectl", "cluster-info"],
            node=self._controlling_node,
        )
        return KubernetesClusterInfo.form_cluster_info_output(raw_output=raw_output)

    def get_nodes(self, selector: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get the nodes currently in the cluster."""
        if selector:
            selector_cli = f"--selector='{selector}'"
        else:
            selector_cli = ""

        output = run_one_as_dict(
            command=["kubectl", "get", "nodes", "--output=json", selector_cli], node=self._controlling_node
        )
        return output["items"]

    def get_node(self, node_hostname: str) -> List[Dict[str, Any]]:
        """Get only info for the the given node."""
        return self.get_nodes(selector=f"kubernetes.io/hostname={node_hostname}")

    def get_pods(self, field_selector: Optional[str] = None) -> Dict[str, Any]:
        """Get pods."""
        if field_selector:
            field_selector_cli = f"--field-selector='{field_selector}'"
        else:
            field_selector_cli = ""

        output = run_one_as_dict(
            command=["kubectl", "get", "pods", "--output=json", field_selector_cli], node=self._controlling_node
        )
        return output["items"]

    def get_pods_for_node(self, node_hostname: str) -> Dict[str, Any]:
        """Get pods for node."""
        return self.get_pods(field_selector=f"spec.nodeName={node_hostname}")

    def drain_node(self, node_hostname: str) -> None:
        """Drain a node, it does not wait for the containers to be stopped though."""
        run_one_raw(
            command=["kubectl", "drain", "--ignore-daemonsets", "--delete-local-data", node_hostname],
            node=self._controlling_node,
        )

    def delete_node(self, node_hostname: str) -> None:
        """Delete a node, it does not drain it, see drain_node for that."""
        current_nodes = self.get_nodes(selector=f"kubernetes.io/hostname={node_hostname}")
        if not current_nodes:
            LOGGER.info("Node %s was not part of this kubernetes cluster, ignoring", node_hostname)

        run_one_raw(command=["kubectl", "delete", "node", node_hostname], node=self._controlling_node)

    def is_node_ready(self, node_hostname: str) -> bool:
        """Ready means in 'Ready' status."""
        node_info = self.get_node(node_hostname=node_hostname)
        if not node_info:
            raise KubernetesNodeNotFound("Unable to find node {node_hostname} in the cluster.")

        try:
            return next(
                condition["status"] == "True"
                for condition in node_info[0]["status"]["conditions"]
                if condition["type"] == "Ready"
            )
        except StopIteration as error:
            raise KubernetesNodeStatusError(
                f"Unable to get 'Ready' condition of node {node_hostname}, got conditions:\n"
                f"{node_info[0]['conditions']}"
            ) from error
