r"""WMCS Toolforge Kubernetes - renew kubeadm certificates

Usage example:
    cookbook wmcs.toolforge.k8s.kubeadm_certs_renew \
        --project tools \
        --control-hostname-list tools-k8s-control1 tools-k8s-control2

See Also:
    https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/#manual-certificate-renewal

"""
import argparse
import logging
import time
from typing import List

from spicerack import Spicerack
from spicerack.cookbook import ArgparseFormatter, CookbookBase
from spicerack.remote import RemoteExecutionError

from wmcs_libs.common import (
    CommonOpts,
    OutputFormat,
    SALLogger,
    WMCSCookbookRunnerBase,
    add_common_opts,
    parser_type_list_hostnames,
    run_one_as_dict,
    run_one_raw,
    with_common_opts,
)

LOGGER = logging.getLogger(__name__)

KUBERNETES_STATIC_POD_DIR = "/etc/kubernetes/manifests/"
KUBELET_CONFIG_FILE = "/var/lib/kubelet/config.yaml"


class ToolforgeK8sKubeadmCertRenew(CookbookBase):
    """Renew kubeadm certs."""

    title = __doc__

    def argument_parser(self):
        """Parse the command line arguments for this cookbook."""
        parser = argparse.ArgumentParser(
            prog=__name__,
            description=__doc__,
            formatter_class=ArgparseFormatter,
        )
        add_common_opts(parser, project_default="toolsbeta")
        parser.add_argument(
            "--control-hostname-list",
            required=True,
            nargs="+",
            type=parser_type_list_hostnames,
            help="List of k8s control nodes to operate on",
        )
        return parser

    def get_runner(self, args: argparse.Namespace) -> WMCSCookbookRunnerBase:
        """Get runner"""
        return with_common_opts(self.spicerack, args, ToolforgeK8sKubeadmCertRenewRunner,)(
            spicerack=self.spicerack,
            control_hostname_list=args.control_hostname_list,
        )


def node_check_leftovers(node) -> None:
    """Quick and dirty check to see if there was a previous unfinished run."""
    command = ["ls", "-ad", f"{KUBERNETES_STATIC_POD_DIR}.*"]
    raw_output = run_one_raw(
        node=node, command=command, print_output=False, print_progress_bars=False, is_safe=True, capture_errors=False
    )
    for line in raw_output.splitlines():
        if line in [f"{KUBERNETES_STATIC_POD_DIR}.", f"{KUBERNETES_STATIC_POD_DIR}.."]:
            continue

        raise ValueError(f"ERROR: {node}: dotfile {line}, perhaps from a previous unclear run. Fix by hand.")


def reset_creation_timestamp(node, static_pod_file: str) -> None:
    """Resets the metadata.creationTimestamp value.

    This doesn't restart the kubelet pod process, just the API metadata about it.
    """
    podname = static_pod_file.rstrip(".yaml") + "-" + str(node).split(".", maxsplit=1)[0]

    command = ["kubectl", "-n", "kube-system", "delete", "pod", podname]
    LOGGER.info("INFO: %s: reset creationTimestamp: %s", node, " ".join(command))
    try:
        result = run_one_raw(
            node=node, command=command, print_output=False, print_progress_bars=False, capture_errors=True
        )
    except RemoteExecutionError as e:
        if not result:
            result = ""

        LOGGER.warning("WARN: %s: %s -- %s", node, str(e), result)


def restart_static_pod(node, static_pod_file: str, wait: int) -> None:
    """Restart a single static pod on a given node.

    See also https://kubernetes.io/docs/tasks/configure-pod-container/static-pod/
    """
    orig = f"{KUBERNETES_STATIC_POD_DIR}{static_pod_file}"
    temp = f"{KUBERNETES_STATIC_POD_DIR}.{static_pod_file}"

    command = ["mv", orig, temp]
    LOGGER.info("INFO: %s: %s", node, " ".join(command))
    run_one_raw(node=node, command=command, print_output=False, print_progress_bars=False)

    LOGGER.info("INFO: %s: waiting %d secs for kubelet to do filecheck for %s", node, wait, orig)
    time.sleep(wait)

    command = ["mv", temp, orig]
    LOGGER.info("INFO: %s: %s", node, " ".join(command))
    run_one_raw(node=node, command=command, print_output=False, print_progress_bars=False)

    LOGGER.info("INFO: %s: waiting %d secs for kubelet to do filecheck for %s", node, wait, orig)
    time.sleep(wait)

    reset_creation_timestamp(node, static_pod_file)


def restart_control_plane_static_pods(node) -> None:
    """Restart k8s control plane static pods."""
    command = ["cat", KUBELET_CONFIG_FILE]
    kubelet_config = run_one_as_dict(
        node=node,
        command=command,
        try_format=OutputFormat.YAML,
        print_output=False,
        print_progress_bars=False,
        is_safe=True,
    )
    kubelet_filecheck_freq = kubelet_config["fileCheckFrequency"]
    LOGGER.info("INFO: %s: figured kubelet fileCheckFrequency to be %s", node, kubelet_filecheck_freq)

    if not kubelet_filecheck_freq.endswith("s"):
        raise ValueError(f"ERROR: {node}: expecting a kubelet fileCheckFrequency like 'XXs'. This code can't handle it")

    wait = 1 + int(kubelet_filecheck_freq.rstrip("s"))

    command = ["ls", KUBERNETES_STATIC_POD_DIR]
    raw_output = run_one_raw(node=node, command=command, print_output=False, print_progress_bars=False, is_safe=True)
    static_pod_file_list = raw_output.splitlines()
    for static_pod_file in static_pod_file_list:
        if not static_pod_file.endswith(".yaml"):
            LOGGER.warning("WARN: %s: ignoring unknown file '%s' (expecting .yaml suffix)", node, static_pod_file)
            continue

        restart_static_pod(node, static_pod_file, wait)


class ToolforgeK8sKubeadmCertRenewRunner(WMCSCookbookRunnerBase):
    """Runner for ToolforgeK8sKubeadmCertRenew."""

    def __init__(
        self,
        common_opts: CommonOpts,
        control_hostname_list: List[str],
        spicerack: Spicerack,
    ):
        """Init"""
        super().__init__(spicerack=spicerack)
        self.common_opts = common_opts
        self.control_hostname_list = control_hostname_list
        self.sallogger = SALLogger(
            project=common_opts.project, task_id=common_opts.task_id, dry_run=common_opts.no_dologmsg
        )

    def run(self) -> None:
        """Main entry point"""
        remote = self.spicerack.remote()
        for node_hostname in self.control_hostname_list:
            node_fqdn = f"{node_hostname}.{self.common_opts.project}.eqiad1.wikimedia.cloud"
            node = remote.query(f"D{{{node_fqdn}}}", use_sudo=True)

            node_check_leftovers(node)

            command = ["kubeadm", "certs", "renew", "all"]
            LOGGER.info("INFO: %s: step 1 -- %s", node, " ".join(command))
            run_one_raw(node=node, command=command, print_output=False, print_progress_bars=False)

            LOGGER.info("INFO: %s: step 2 -- restart control plane static pods", node)
            restart_control_plane_static_pods(node)

            self.sallogger.log(message=f"renewed kubeadm certs on {node_hostname}")
