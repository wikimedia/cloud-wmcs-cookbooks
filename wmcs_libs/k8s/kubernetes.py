#!/usr/bin/env python3
"""Generic kubernetes managing code."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Any, Generator

from spicerack.remote import Remote

from wmcs_libs.common import CuminParams, run_one_as_dict, run_one_raw

LOGGER = logging.getLogger(__name__)

# TODO: the semantic of this is not clear. Namespaces with DaemonSets?
# TODO: (cont) or perhaps just namespaces which can be ignored in drain ops?
K8S_SYSTEM_NAMESPACES = ["kube-system", "metrics"]


class KubernetesError(Exception):
    """Parent class for all kubernetes related errors."""


class KubernetesMalformedClusterInfo(KubernetesError):
    """Risen when the gotten cluster info is not formatted as expected."""


class KubernetesNodeNotFound(KubernetesError):
    """Risen when the given node does not exist."""


class KubernetesNodeStatusError(KubernetesError):
    """Risen when the given node status is not recognized."""


class KubernetesTimeoutForNotReady(KubernetesError):
    """Risen when there is a timeout waiting for a node to become READY."""


class KubernetesTimeoutForDrain(KubernetesError):
    """Risen when there is a timeout waiting for a node to drain."""


class KubernetesRebootNodePhase(Enum):
    """Enum to represent a k8s node reboot phase/stage."""

    DRAIN = auto()
    WAIT_DRAIN = auto()
    VM_REBOOT = auto()
    UNCORDON = auto()
    WAIT_READY = auto()
    DONE = auto()

    def __str__(self):
        """String representation."""
        return self.name.lower()


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

    def get_nodes(self, selector: str | None = None) -> list[dict[str, Any]]:
        """Get the nodes currently in the cluster."""
        if selector:
            selector_cli = f"--selector='{selector}'"
        else:
            selector_cli = ""

        output = run_one_as_dict(
            command=["kubectl", "get", "nodes", "--output=json", selector_cli],
            node=self._controlling_node,
            cumin_params=CuminParams(is_safe=True, print_output=False, print_progress_bars=False),
        )
        return output["items"]

    def get_nodes_hostnames(self, selector: str | None = None) -> list[str]:
        """Get the list of nodes currently in the cluster, in hostname list fashion."""
        hostname_list = []
        for item in self.get_nodes(selector):
            hostname_list.append(item["metadata"]["name"])

        return hostname_list

    def get_node(self, node_hostname: str) -> list[dict[str, Any]]:
        """Get only info for the the given node."""
        return self.get_nodes(selector=f"kubernetes.io/hostname={node_hostname}")

    def get_pods(self, field_selector: str | None = None) -> list[dict[str, Any]]:
        """Get pods."""
        if field_selector:
            field_selector_cli = f"--field-selector='{field_selector}'"
        else:
            field_selector_cli = ""

        output = run_one_as_dict(
            command=["kubectl", "get", "pods", "--output=json", field_selector_cli],
            node=self._controlling_node,
            cumin_params=CuminParams(is_safe=True, print_output=False, print_progress_bars=False),
        )
        return output["items"]

    def get_pods_for_node(self, node_hostname: str) -> list[dict[str, Any]]:
        """Get pods for node."""
        return self.get_pods(field_selector=f"spec.nodeName={node_hostname}")

    def get_non_system_pods_for_node(self, node_hostname: str) -> list[dict[str, Any]]:
        """Get all non-system pods in a node."""
        pods = self.get_pods_for_node(node_hostname=node_hostname)
        return [pod for pod in pods if pod["metadata"]["namespace"] not in K8S_SYSTEM_NAMESPACES]

    def drain_node(self, node_hostname: str) -> None:
        """Drain a node, it does not wait for the containers to be stopped though."""
        node_info = self.get_node(node_hostname=node_hostname)
        if not node_info:
            raise KubernetesNodeNotFound("Unable to find node {node_hostname} in the cluster.")

        run_one_raw(
            command=["kubectl", "drain", "--ignore-daemonsets", "--delete-local-data", node_hostname],
            node=self._controlling_node,
        )

    def wait_for_drain(self, node_hostname: str, check_interval_seconds: int = 10, timeout_seconds: int = 300) -> None:
        """Wait for a given node to be completely drained of pods."""
        start_time = time.time()
        cur_time = start_time
        while cur_time - start_time < timeout_seconds:
            non_system_pods = self.get_non_system_pods_for_node(node_hostname)
            if not non_system_pods:
                return

            LOGGER.debug(
                "Waiting for node %s to stop all it's pods, still %d running ...",
                node_hostname,
                len(non_system_pods),
            )

            time.sleep(check_interval_seconds)
            cur_time = time.time()

        # timed out!
        raise KubernetesTimeoutForDrain(
            f"Waited {timeout_seconds} for node {node_hostname} to drain, but it never did. "
            f"Still has {len(non_system_pods)} pods running. Running pods:\n"
            f"{json.dumps(non_system_pods, indent=4)}"
        )

    def delete_node(self, node_hostname: str) -> None:
        """Delete a node, it does not drain it, see drain_node for that."""
        node_info = self.get_node(node_hostname=node_hostname)
        if not node_info:
            raise KubernetesNodeNotFound(f"Unable to find node {node_hostname} in the cluster.")

        run_one_raw(command=["kubectl", "delete", "node", node_hostname], node=self._controlling_node)

    def uncordon_node(self, node_hostname: str) -> None:
        """Uncordon a node."""
        current_nodes = self.get_nodes(selector=f"kubernetes.io/hostname={node_hostname}")
        if not current_nodes:
            raise KubernetesNodeNotFound("Unable to find node {node_hostname} in the cluster.")

        run_one_raw(
            command=["kubectl", "uncordon", node_hostname],
            node=self._controlling_node,
            cumin_params=CuminParams(print_output=False, print_progress_bars=False),
        )

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

    def wait_for_ready(self, node_hostname: str, check_interval_seconds: int = 10, timeout_seconds: int = 600) -> None:
        """Wait for a given k8s node to be in READY status."""
        start_time = time.time()
        cur_time = start_time

        while cur_time - start_time < timeout_seconds:
            if self.is_node_ready(node_hostname):
                return

            time.sleep(check_interval_seconds)
            cur_time = time.time()

        # timed out!
        cur_conditions = self.get_node(node_hostname)[0]["conditions"]
        raise KubernetesTimeoutForNotReady(
            f"Waited {timeout_seconds} for node {node_hostname} to "
            "become healthy, but it never did. Current conditions:\n"
            f"{json.dumps(cur_conditions, indent=4)}"
        )

    def reboot_node(
        self, node_hostname: str, domain: str
    ) -> Generator[KubernetesRebootNodePhase, KubernetesRebootNodePhase, KubernetesRebootNodePhase]:
        """Reboot k8s node."""
        yield KubernetesRebootNodePhase.DRAIN
        self.drain_node(node_hostname)

        yield KubernetesRebootNodePhase.WAIT_DRAIN
        self.wait_for_drain(node_hostname)

        yield KubernetesRebootNodePhase.VM_REBOOT
        node_fqdn = f"{node_hostname}.{domain}"
        node = self._remote.query(f"D{{{node_fqdn}}}", use_sudo=True)
        reboot_time = datetime.utcnow()
        node.reboot()
        node.wait_reboot_since(since=reboot_time)

        yield KubernetesRebootNodePhase.UNCORDON
        self.uncordon_node(node_hostname)

        yield KubernetesRebootNodePhase.WAIT_READY
        self.wait_for_ready(node_hostname)

        return KubernetesRebootNodePhase.DONE
