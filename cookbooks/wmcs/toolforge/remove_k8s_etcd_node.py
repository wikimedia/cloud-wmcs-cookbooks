r"""WMCS Toolforge - Depool and delete the given etcd node from a toolforge installation

Usage example:
    cookbook wmcs.toolforge.remove_k8s_etcd_node \
        --cluster-name toolsbeta \
        --node-fqdn toolsbeta-test-etcd-8.toolsbeta.eqiad1.wikimedia.cloud

"""
from __future__ import annotations

import logging

from cookbooks.wmcs.toolforge.k8s.etcd.depool_and_remove_node import ToolforgeDepoolAndRemoveNode

LOGGER = logging.getLogger(__name__)


class ToolforgeRemoveK8sEtcdNode(ToolforgeDepoolAndRemoveNode):
    """WMCS Toolforge cookbook to remove and delete an existing K8s etcd node"""

    title = __doc__
