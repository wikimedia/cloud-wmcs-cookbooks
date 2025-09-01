#!/usr/bin/env python3
"""Generic kubernetes managing code."""
from __future__ import annotations

import json
import logging
import time
from argparse import ArgumentTypeError
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Any, Generator, Literal, overload

from spicerack.remote import Remote, RemoteExecutionError

from wmcs_libs.common import CuminParams, OutputFormat, run_one_as_dict, run_one_raw
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName
from wmcs_libs.k8s.clusters import get_control_nodes

LOGGER = logging.getLogger(__name__)


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


class KubeletStaticPodNotFound(KubernetesError):
    """Risen when there is no such static pod defined in a kubelet."""


class KubeletUnexpectedStaticPodPathStatus(KubernetesError):
    """Risen when an unexpected kubelet static pod path status."""


class KubeletUnexpectedConfig(KubernetesError):
    """Risen when an unexpected kubelet config is found."""


class KubeletUnableToStopStaticPod(KubernetesError):
    """Risen when the system failed to stop a static pod."""


class KubeletUnableToStartStaticPod(KubernetesError):
    """Risen when the system failed to start a static pod."""


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
        Kubernetes control plane is running at https://k8s.svc.toolsbeta.eqiad1.wikimedia.cloud:6443  # noqa: E501
        CoreDNS is running at https://k8s.svc.toolsbeta.eqiad1.wikimedia.cloud:6443/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy  # noqa: E501
        Metrics-server is running at https://k8s.svc.toolsbeta.eqiad1.wikimedia.cloud:6443/api/v1/namespaces/kube-system/services/https:metrics-server:/proxy  # noqa: E501

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


@dataclass(frozen=True)
class KubernetesNodeInfo:
    """Kubernetes node information."""

    kubelet_version: str

    @classmethod
    def from_node_status(cls, status: dict[str, Any]) -> "KubernetesNodeInfo":
        """Constructor."""
        return cls(kubelet_version=status["nodeInfo"]["kubeletVersion"].removeprefix("v"))


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

    @overload
    def get_object(self, kind: str, name: str, namespace: str, *, missing_ok: Literal[False] = False) -> dict[str, Any]:
        pass

    @overload
    def get_object(
        self, kind: str, name: str, namespace: str, *, missing_ok: Literal[True] = True
    ) -> dict[str, Any] | None:
        pass

    @overload
    def get_object(self, kind: str, name: str, namespace: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        pass

    def get_object(self, kind: str, name: str, namespace: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        """Get data for a single object in the cluster."""
        try:
            return run_one_as_dict(
                command=["kubectl", "get", kind, name, f"--namespace={namespace}", "--output=json"],
                node=self._controlling_node,
                cumin_params=CuminParams(is_safe=True, print_output=False, print_progress_bars=False),
            )
        except RemoteExecutionError:
            if missing_ok:
                return None
            raise

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

    def get_node_info(self, node_hostname: str) -> KubernetesNodeInfo:
        """Get parsed metadata about the given node."""
        node_data = self.get_node(node_hostname=node_hostname)
        if not node_data:
            raise KubernetesNodeNotFound(f"Unable to find node {node_hostname} in the cluster.")

        return KubernetesNodeInfo.from_node_status(node_data[0]["status"])

    def get_pods(self, namespace: str | None = None, field_selector: str | None = None) -> list[dict[str, Any]]:
        """Get pods."""
        namespace_arg = f"--namespace='{namespace}'" if namespace else "--all-namespaces"
        field_selector_args = [f"--field-selector='{field_selector}'"] if field_selector else []

        output = run_one_as_dict(
            command=["kubectl", "get", "pods", namespace_arg, "--output=json", *field_selector_args],
            node=self._controlling_node,
            cumin_params=CuminParams(is_safe=True, print_output=False, print_progress_bars=False),
        )
        return output["items"]

    def get_pods_for_node(self, node_hostname: str, namespace: str | None = None) -> list[dict[str, Any]]:
        """Get pods for node."""
        return self.get_pods(namespace=namespace, field_selector=f"spec.nodeName={node_hostname}")

    def get_evictable_pods_for_node(self, node_hostname: str) -> list[dict[str, Any]]:
        """Get all pods in a node which will be evicted when draining the node."""
        pods = self.get_pods_for_node(node_hostname=node_hostname)
        return [
            pod
            for pod in pods
            if not any(
                # DaemonSets run on every node so they can't be evicted from individual nodes.
                # The control plane components (api-server, controller-manager, scheduler) run
                # with static manifests that are read by Kubelet, and marked as owned by the Node
                # resource. They can't be evicted either.
                # We assume that everything else can be evicted.
                ref["kind"] == "Node" or ref["kind"] == "DaemonSet"
                for ref in pod["metadata"].get("ownerReferences", [])
            )
        ]

    def drain_node(self, node_hostname: str, timeout_seconds: int = 60) -> None:
        """Drain a node, it does not wait for the containers to be stopped though."""
        node_info = self.get_node(node_hostname=node_hostname)
        if not node_info:
            raise KubernetesNodeNotFound(f"Unable to find node {node_hostname} in the cluster.")

        command = [
            "kubectl",
            "drain",
            "--ignore-daemonsets",
            "--delete-emptydir-data",
            "--grace-period=1",
            "--skip-wait-for-delete-timeout=1",
            f"--timeout={timeout_seconds}s",
            "--force",
            node_hostname,
        ]

        run_one_raw(command=command, node=self._controlling_node)

    def wait_for_drain(self, node_hostname: str, check_interval_seconds: int = 10, timeout_seconds: int = 300) -> None:
        """Wait for a given node to be completely drained of pods."""
        start_time = time.time()
        cur_time = start_time
        while cur_time - start_time < timeout_seconds:
            evictable_pods = self.get_evictable_pods_for_node(node_hostname)
            if not evictable_pods:
                return

            LOGGER.debug(
                "Waiting for node %s to stop all it's pods, still %d running ...",
                node_hostname,
                len(evictable_pods),
            )

            time.sleep(check_interval_seconds)
            cur_time = time.time()

        # timed out!
        raise KubernetesTimeoutForDrain(
            f"Waited {timeout_seconds} for node {node_hostname} to drain, but it never did. "
            f"Still has {len(evictable_pods)} pods running. Running pods:\n"
            f"{json.dumps(evictable_pods, indent=4)}"
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
            raise KubernetesNodeNotFound(f"Unable to find node {node_hostname} in the cluster.")

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

    def get_deployment(self, deployment: str, namespace: str) -> dict[str, Any]:
        return run_one_as_dict(
            command=["kubectl", "-n", namespace, "get", "deployment", "-o", "json", deployment],
            node=self._controlling_node,
            try_format=OutputFormat.JSON,
            cumin_params=CuminParams(is_safe=True, print_output=False, print_progress_bars=False),
        )

    def wait_for_deployment_replicas(
        self,
        deployment: str,
        namespace: str,
        num_replicas: int,
        check_interval_seconds: int = 10,
        timeout_seconds: int = 600,
    ) -> None:
        """Wait for a given k8s node to be in READY status."""
        start_time = time.time()
        cur_time = start_time

        while cur_time - start_time < timeout_seconds:
            deployment_data = self.get_deployment(deployment=deployment, namespace=namespace)
            if deployment_data["status"]["replicas"] == num_replicas:
                return

            time.sleep(check_interval_seconds)
            cur_time = time.time()

        # timed out!
        deployment_data = self.get_deployment(deployment=deployment, namespace=namespace)
        cur_conditions = deployment_data["conditions"]
        raise KubernetesTimeoutForNotReady(
            f"Waited {timeout_seconds} for deployment {deployment} (namespace:{namespace}) to "
            "have {num_replicas} replicas, but it never did. Current conditions:\n"
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

    def add_node_labels(self, node_hostname: str, labels: set[str]) -> None:
        """Add the specified labels to a node."""
        run_one_raw(command=["kubectl", "label", "node", node_hostname, *labels], node=self._controlling_node)

    def add_node_taints(self, node_hostname: str, taints: set[str]) -> None:
        """Add the specified labels to a node."""
        run_one_raw(command=["kubectl", "taint", "node", node_hostname, *taints], node=self._controlling_node)

    def delete_pod(self, pod_name: str, namespace: str) -> None:
        """Delete the given pod."""
        command = ["kubectl", "delete", "--namespace", namespace, "pod", pod_name]
        run_one_raw(
            node=self._controlling_node,
            command=command,
            capture_errors=False,
            cumin_params=CuminParams(print_output=False, print_progress_bars=False),
        )

    def is_pod_running(self, pod_name: str, namespace: str, missing_ok: bool = False) -> bool:
        """Check if a pod is in running state."""
        pod_dict = self.get_object(kind="pod", name=pod_name, namespace=namespace, missing_ok=missing_ok)
        if not pod_dict:
            # we rely on get_object() to raise the error if not missing_ok
            return False

        return pod_dict.get("status", {}).get("phase", "") == "Running"

    @staticmethod
    def pick_a_control_node(cluster_name: ToolforgeKubernetesClusterName, skip_hostname: str = "") -> str:
        domain = f"{cluster_name.get_openstack_cluster_name()}.wikimedia.cloud"
        fqdn = f"{skip_hostname}.{cluster_name.get_project()}.{domain}"
        LOGGER.debug("Finding next control node that is not %s", fqdn)
        return next(control_node for control_node in get_control_nodes(cluster_name) if control_node != fqdn)

    def scale_deployment(self, deployment: str, new_replicas: int, namespace: str) -> None:
        run_one_raw(
            command=["kubectl", "-n", namespace, "scale", "deployment", deployment, f"--replicas={new_replicas}"],
            node=self._controlling_node,
        )


class KubeletController:
    """Controller for a given kubelet daemon."""

    def __init__(
        self,
        remote: Remote,
        kubelet_node_fqdn: str,
        k8s_control: KubernetesController,
        kubelet_config_path: str = "/var/lib/kubelet/config.yaml",
    ):
        """Init."""
        self._remote = remote
        self.kubelet_node_fqdn = kubelet_node_fqdn
        self.kubelet_node_short_hostname = kubelet_node_fqdn.split(".", 1)[0]
        self.k8s_control = k8s_control
        self.kubelet_config_path = kubelet_config_path
        self._kubelet_node = self._remote.query(f"D{{{self.kubelet_node_fqdn}}}", use_sudo=True)
        self._static_pod_stopped_prefix = ".cookbook-stopped-"
        self._kubelet_config_cache: dict[str, Any] = {}

    def get_kubelet_config(self) -> dict[str, Any]:
        """Get the kubelet configuration."""
        # some basic caching
        if not self._kubelet_config_cache:
            self._kubelet_config_cache = run_one_as_dict(
                command=["cat", self.kubelet_config_path],
                node=self._kubelet_node,
                try_format=OutputFormat.YAML,
                cumin_params=CuminParams(is_safe=True, print_output=False, print_progress_bars=False),
            )

        return self._kubelet_config_cache

    def get_kubelet_config_parameter(self, param: str) -> Any:
        """Get a given config parameter."""
        try:
            return self.get_kubelet_config()[param]
        except KeyError as e:
            raise KubeletUnexpectedConfig(f"couldn't find config parameter {param} in kubelet config: {str(e)}") from e

    def get_kubelet_filecheckfrequency(self) -> int:
        """Get fileCheckFrequency config parameter."""
        raw_value = str(self.get_kubelet_config_parameter("fileCheckFrequency"))
        if not raw_value.endswith("s"):
            raise KubeletUnexpectedConfig("the fileCheckFrequency value doesn't end with a 's' characted")

        return int(raw_value.rstrip("s"))

    def get_static_pods_defined(self) -> list[str]:
        """Get a list of static pods defined in this kubelet."""
        static_pods_path = str(self.get_kubelet_config_parameter("staticPodPath"))
        files = run_one_raw(
            command=["ls", f"{static_pods_path}/*.yaml"],
            node=self._kubelet_node,
            cumin_params=CuminParams(is_safe=True, print_output=False, print_progress_bars=False),
        )
        return [file.removesuffix(".yaml").removeprefix(f"{static_pods_path}/") for file in files.splitlines()]

    def assert_static_pod_path_clean(self) -> None:
        """Quick and dirty check to see if there was a previous unfinished run."""
        static_pod_path = str(self.get_kubelet_config_parameter("staticPodPath"))
        command = ["ls", "-ad", f"{static_pod_path}/.*"]
        try:
            raw_output = run_one_raw(
                node=self._kubelet_node,
                command=command,
                capture_errors=False,
                cumin_params=CuminParams(print_output=False, print_progress_bars=False, is_safe=True),
            )
        except RemoteExecutionError:
            # if this fails, there is usually nothing in here, ignore
            return

        allowed_entries = [
            f"{static_pod_path}/.",
            f"{static_pod_path}/..",
            # .kubelet-keep can exist after a package upgrade
            f"{static_pod_path}/.kubelet-keep",
        ]

        for line in raw_output.splitlines():
            if line in allowed_entries:
                continue

            raise KubeletUnexpectedStaticPodPathStatus(f"path '{static_pod_path}' contains cruft. Fix by hand: {line}")

    def assert_static_pod_is_defined(self, pod_name: str) -> None:
        """Asserts whether a static pod is defined."""
        if pod_name not in self.get_static_pods_defined():
            raise KubeletStaticPodNotFound(f"static pod {pod_name} doesn't seem to be defined in this kubelet")

    def _static_pod_runtime_name(self, short_pod_name: str) -> str:
        """Returns the full runtime name of a static pod."""
        return f"{short_pod_name}-{self.kubelet_node_short_hostname}"

    def stop_static_pod(self, pod_name: str, namespace: str) -> None:
        """Stop a static pod running in this kubelet."""
        self.assert_static_pod_is_defined(pod_name)

        static_pod_path = self.get_kubelet_config_parameter("staticPodPath")
        orig = f"{static_pod_path}/{pod_name}.yaml"
        dest = f"{static_pod_path}/{self._static_pod_stopped_prefix}{pod_name}.yaml"

        command = ["mv", orig, dest]
        run_one_raw(
            node=self._kubelet_node,
            command=command,
            cumin_params=CuminParams(print_output=False, print_progress_bars=False),
        )

        time.sleep(self.get_kubelet_filecheckfrequency())

        try:
            # reset the metadata.creationTimestamp value
            self.k8s_control.delete_pod(pod_name=self._static_pod_runtime_name(pod_name), namespace=namespace)
        except RemoteExecutionError:
            # we don't care if this fails, this step is actually optional
            pass

        try:
            self.assert_static_pod_is_defined(pod_name)
            if self.k8s_control.is_pod_running(
                pod_name=self._static_pod_runtime_name(pod_name), namespace=namespace, missing_ok=True
            ):
                # pod is still running? we failed to stop it
                raise KubeletUnableToStopStaticPod(f"we somehow failed to stop static pod {pod_name}")
        except KubeletStaticPodNotFound:
            # expected, the pod was sucessfully stopped
            pass

    def start_static_pod(self, pod_name: str, namespace: str) -> None:
        """Start a previously stopped static pod."""
        try:
            self.assert_static_pod_is_defined(pod_name)
            if self.k8s_control.is_pod_running(
                pod_name=self._static_pod_runtime_name(pod_name), namespace=namespace, missing_ok=True
            ):
                # pod is already running doing nothing
                return
        except KubeletStaticPodNotFound:
            # expected, the pod was previously stopped
            pass

        static_pod_path = self.get_kubelet_config_parameter("staticPodPath")
        orig = f"{static_pod_path}/{self._static_pod_stopped_prefix}{pod_name}.yaml"
        dest = f"{static_pod_path}/{pod_name}.yaml"

        command = ["mv", orig, dest]
        run_one_raw(
            node=self._kubelet_node,
            command=command,
            cumin_params=CuminParams(print_output=False, print_progress_bars=False),
        )

        time.sleep(self.get_kubelet_filecheckfrequency())

        try:
            self.assert_static_pod_is_defined(pod_name)
        except KubeletStaticPodNotFound as e:
            raise KubeletUnableToStartStaticPod(f"we failed to start static pod {pod_name}: {str(e)}") from e

        if not self.k8s_control.is_pod_running(
            pod_name=self._static_pod_runtime_name(pod_name), namespace=namespace, missing_ok=True
        ):
            raise KubeletUnableToStartStaticPod(f"we failed to start static pod {pod_name}")

    def restart_static_pod(self, pod_name: str, namespace: str) -> None:
        """Restart a given static pod.

        See also https://kubernetes.io/docs/tasks/configure-pod-container/static-pod/
        """
        self.stop_static_pod(pod_name, namespace)
        self.start_static_pod(pod_name, namespace)

    def restart_all_static_pods(self, namespace: str) -> None:
        """Restart all static pods."""
        self.assert_static_pod_path_clean()

        pod_list = self.get_static_pods_defined()

        if "kube-apiserver" in pod_list:
            # restart the kube-apiserver first, then the rest of the pods
            # this should account for an ugly error in scheduler and controller-manager
            # if they start before the apiserver
            self.restart_static_pod("kube-apiserver", namespace)
            pod_list.remove("kube-apiserver")

        for pod in pod_list:
            self.restart_static_pod(pod, namespace)


def validate_version(version: str) -> str:
    """Argparse type validator for Kubernetes versions."""
    parts = [part.strip() for part in version.split(".") if part.strip() != "" and part.strip().isnumeric()]
    if len(parts) != 3:
        raise ArgumentTypeError(f"Expected version in minor.major.patch format, got '{version}'")
    return ".".join(parts)
