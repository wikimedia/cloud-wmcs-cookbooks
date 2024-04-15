r"""WMCS Toolforge - Depool and delete the given k8s worker node from a Toolforge cluster

Usage example:
    cookbook wmcs.toolforge.remove_k8s_node \
        --cluster-name toolsbeta \
        --role worker \
        --hostname-to-remove toolsbeta-test-worker-4

"""

from __future__ import annotations

import logging

from cookbooks.wmcs.toolforge.k8s.worker.depool_and_remove_node import ToolforgeDepoolAndRemoveNode

LOGGER = logging.getLogger(__name__)


class ToolforgeRemoveK8sNode(ToolforgeDepoolAndRemoveNode):
    """WMCS Toolforge cookbook to remove and delete an existing k8s node"""

    title = __doc__
