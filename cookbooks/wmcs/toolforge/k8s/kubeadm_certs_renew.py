r"""WMCS Toolforge Kubernetes - renew kubeadm certificates

Usage example:
    cookbook wmcs.toolforge.k8s.kubeadm_certs_renew \
        --project tools \
        --control-hostname-list tools-k8s-control1 tools-k8s-control2

See Also:
    https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/#manual-certificate-renewal

"""

from __future__ import annotations

import argparse
import logging

from spicerack import Spicerack
from spicerack.cookbook import CookbookBase

from wmcs_libs.common import (
    CommonOpts,
    CuminParams,
    WMCSCookbookRunnerBase,
    add_common_opts,
    parser_type_list_hostnames,
    run_one_raw,
    with_common_opts,
)
from wmcs_libs.k8s.kubernetes import KubeletController, KubernetesController

LOGGER = logging.getLogger(__name__)


class ToolforgeK8sKubeadmCertRenew(CookbookBase):
    """Renew kubeadm certs."""

    def argument_parser(self):

        parser = super().argument_parser()
        add_common_opts(parser, project_default="toolsbeta")
        parser.add_argument(
            "--control-hostname-list",
            required=True,
            nargs="+",
            type=parser_type_list_hostnames,
            help="list of k8s control nodes to operate on",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:

        return with_common_opts(
            self.spicerack,
            args,
            ToolforgeK8sKubeadmCertRenewRunner,
        )(
            spicerack=self.spicerack,
            control_hostname_list=args.control_hostname_list,
        )


class ToolforgeK8sKubeadmCertRenewRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        control_hostname_list: list[str],
        spicerack: Spicerack,
    ):

        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.common_opts = common_opts
        self.control_hostname_list = control_hostname_list

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        return f"for {', '.join(self.control_hostname_list)}"

    def run(self) -> None:

        remote = self.spicerack.remote()
        for node_hostname in self.control_hostname_list:
            node_fqdn = f"{node_hostname}.{self.common_opts.project}.eqiad1.wikimedia.cloud"
            node = remote.query(f"D{{{node_fqdn}}}", use_sudo=True)

            command = ["kubeadm", "certs", "renew", "all"]
            LOGGER.info("INFO: %s: step 1 -- %s", node, " ".join(command))
            run_one_raw(
                node=node, command=command, cumin_params=CuminParams(print_output=False, print_progress_bars=False)
            )

            LOGGER.info("INFO: %s: step 2 -- restart control plane static pods", node)

            k8s_control = KubernetesController(remote=remote, controlling_node_fqdn=node_fqdn)
            kubelet = KubeletController(remote=remote, kubelet_node_fqdn=node_fqdn, k8s_control=k8s_control)
            kubelet.restart_all_static_pods(namespace="kube-system")
