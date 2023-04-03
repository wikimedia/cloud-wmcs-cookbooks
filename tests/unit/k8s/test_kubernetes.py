from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wmcs_libs.common import UtilsForTesting
from wmcs_libs.k8s.kubernetes import KubernetesController


def test_KubernetesController_get_evictable_pods_for_node(monkeypatch):
    fake_remote = UtilsForTesting.get_fake_remote()
    controller = KubernetesController(remote=fake_remote, controlling_node_fqdn="fake.example")

    def fake_get_pods_for_node(node_hostname: str) -> list[dict[str, Any]]:
        with (Path(__file__).parent / ".." / "fixtures" / "k8s" / "control-node-pods.json").open("r") as f:
            pods = json.load(f)
        return pods

    monkeypatch.setattr(controller, "get_pods_for_node", fake_get_pods_for_node)

    evictable_pods = [pod["metadata"]["name"] for pod in controller.get_evictable_pods_for_node("fake.example")]
    # The test data in question is a snapshot of pods running on a control plane
    # node. Most of them can't be evicted, since they are either Kubernetes or
    # Calico components, but at the time there was a CoreDNS pod on the node, which
    # is managed by a Deployment and so can be evicted.
    assert evictable_pods == ["coredns-796684d57c-cnfxl"]
