r"""WMCS Toolforge - Depool and delete the given etcd node from a toolforge installation

Usage example:
    cookbook wmcs.toolforge.k8s.etcd.depool_and_remove_node \
        --cluster-name toolsbeta \
        --node-fqdn toolsbeta-test-etcd-8.toolsbeta.eqiad1.wikimedia.cloud

"""

from __future__ import annotations

import argparse
import base64
import logging
import time

import yaml
from spicerack import Spicerack
from spicerack.cookbook import CookbookBase
from spicerack.remote import Remote, RemoteHosts

from cookbooks.wmcs.toolforge.k8s.etcd.remove_node_from_hiera import RemoveNodeFromHiera
from cookbooks.wmcs.vps.refresh_puppet_certs import RefreshPuppetCerts
from cookbooks.wmcs.vps.remove_instance import RemoveInstance
from wmcs_libs.common import (
    CommonOpts,
    OutputFormat,
    WMCSCookbookRunnerBase,
    natural_sort_key,
    run_one_as_dict,
    run_one_raw,
    simple_create_file,
)
from wmcs_libs.inventory.toolsk8s import ToolforgeKubernetesClusterName, ToolforgeKubernetesNodeRoleName
from wmcs_libs.k8s.clusters import (
    add_toolforge_kubernetes_cluster_opts,
    get_cluster_node_prefix,
    get_control_nodes,
    with_toolforge_kubernetes_cluster_opts,
)
from wmcs_libs.openstack.common import OpenstackAPI

LOGGER = logging.getLogger(__name__)


class ToolforgeDepoolAndRemoveNode(CookbookBase):
    __doc__ = __doc__

    def argument_parser(self):

        parser = super().argument_parser()
        add_toolforge_kubernetes_cluster_opts(parser)
        parser.add_argument(
            "--fqdn-to-remove",
            required=False,
            help="FQDN of the node to remove, if none passed will remove the instance with the lower index.",
        )
        parser.add_argument(
            "--skip-etcd-certs-refresh",
            action="store_true",
            help=(
                "Skip all the etcd certificate refreshing, useful if you "
                "already did it and you are rerunning, or if you did it "
                "manually"
            ),
        )

        return parser

    def get_runner(self, args: argparse.Namespace) -> "ToolforgeDepoolAndRemoveNodeRunner":

        return with_toolforge_kubernetes_cluster_opts(
            self.spicerack,
            args,
            ToolforgeDepoolAndRemoveNodeRunner,
        )(
            fqdn_to_remove=args.fqdn_to_remove,
            skip_etcd_certs_refresh=args.skip_etcd_certs_refresh,
            spicerack=self.spicerack,
        )


def _fix_apiserver_yaml(node: RemoteHosts, etcd_members: list[str]):
    members_urls = [f"https://{fqdn}:2379" for fqdn in etcd_members]
    new_etcd_members_arg = "--etcd-servers=" + ",".join(sorted(members_urls, key=natural_sort_key))
    apiserver_config_file = "/etc/kubernetes/manifests/kube-apiserver.yaml"
    apiserver_config = run_one_as_dict(
        node=node, command=["cat", f"'{apiserver_config_file}'"], try_format=OutputFormat.YAML
    )
    # we expect the container to be the first and only in the spec
    command_args = apiserver_config["spec"]["containers"][0]["command"]
    for index, arg in enumerate(command_args):
        if arg.startswith("--etcd-servers="):
            if arg == new_etcd_members_arg:
                LOGGER.info("Apiserver yaml file was already ok on %s", node)
                return

            command_args[index] = new_etcd_members_arg
            apiserver_config_str = yaml.dump(apiserver_config)
            simple_create_file(
                remote_path=apiserver_config_file,
                dst_node=node,
                contents=apiserver_config_str,
                use_root=True,
            )
            LOGGER.info("Fixed apiserver yaml file on %s.", node)
            return


def _remove_node_from_kubeadm_configmap(k8s_control_node: RemoteHosts, etcd_fqdn_to_remove: str) -> str:
    namespace = "kube-system"
    configmap = "kubeadm-config"
    kubeadm_config = run_one_as_dict(
        node=k8s_control_node,
        command=["kubectl", f"--namespace='{namespace}'", "get", "configmap", configmap, "-o", "yaml"],
        try_format=OutputFormat.YAML,
    )
    # double yaml yep xd
    cluster_config = yaml.safe_load(kubeadm_config["data"]["ClusterConfiguration"])

    old_endpoint = f"https://{etcd_fqdn_to_remove}:2379"
    if old_endpoint in cluster_config["etcd"]["external"]["endpoints"]:
        cluster_config["etcd"]["external"]["endpoints"].pop(
            cluster_config["etcd"]["external"]["endpoints"].index(old_endpoint)
        )
    else:
        LOGGER.info("Kubeadm configmap %s/%s was already ok.", namespace, configmap)
        return ""

    kubeadm_config["data"]["ClusterConfiguration"] = yaml.dump(cluster_config)
    kubeadm_config["metadata"] = {
        "name": configmap,
        "namespace": namespace,
    }
    kubeadm_config_str = yaml.dump(kubeadm_config)
    # avoid quoting/bash escaping issues
    kubeadm_config_base64 = base64.b64encode(kubeadm_config_str.encode("utf8"))
    return run_one_raw(
        node=k8s_control_node,
        command=[
            f"echo '{kubeadm_config_base64.decode()}'",
            "| base64 --decode",
            "| sudo -i kubectl apply --filename=-",
        ],
    )


def _fix_kubeadm(
    remote: Remote,
    k8s_control_members: list[str],
    etcd_fqdn_to_remove: str,
    etcd_members: list[str],
):
    for k8s_control_node_fqdn in k8s_control_members:
        _fix_apiserver_yaml(
            node=remote.query(f"D{{{k8s_control_node_fqdn}}}", use_sudo=True),
            etcd_members=etcd_members,
        )
        # give time for etcd to stabilize
        time.sleep(10)

    # just pick the first, any should do
    k8s_control_node = remote.query(f"D{{{k8s_control_members[0]}}}", use_sudo=True)
    _remove_node_from_kubeadm_configmap(
        k8s_control_node=k8s_control_node,
        etcd_fqdn_to_remove=etcd_fqdn_to_remove,
    )


class ToolforgeDepoolAndRemoveNodeRunner(WMCSCookbookRunnerBase):

    def __init__(
        self,
        common_opts: CommonOpts,
        cluster_name: ToolforgeKubernetesClusterName,
        spicerack: Spicerack,
        fqdn_to_remove: str,
        skip_etcd_certs_refresh: bool,
    ):

        self.common_opts = common_opts
        self.cluster_name = cluster_name
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.fqdn_to_remove = fqdn_to_remove
        self.skip_etcd_certs_refresh = skip_etcd_certs_refresh
        self.openstack_api = OpenstackAPI(
            remote=spicerack.remote(),
            cluster_name=self.cluster_name.get_openstack_cluster_name(),
            project=self.common_opts.project,
        )

    def run(self) -> None:

        remote = self.spicerack.remote()

        etcd_prefix = get_cluster_node_prefix(self.cluster_name, ToolforgeKubernetesNodeRoleName.ETCD)

        if not self.fqdn_to_remove:
            all_project_servers = self.openstack_api.server_list()
            prefix_members = list(
                sorted(
                    (server for server in all_project_servers if server.get("Name", "noname").startswith(etcd_prefix)),
                    key=lambda server: natural_sort_key(server.get("Name", "noname-0")),
                )
            )
            if not prefix_members:
                raise Exception(
                    f"No servers in project {self.common_opts.project} with prefix {etcd_prefix}, nothing to remove."
                )

            # TODO: find a way to not hardcode the domain
            domain = f"{self.cluster_name.get_openstack_cluster_name()}.wikimedia.cloud"
            fqdn_to_remove = f"{prefix_members[0]['Name']}.{self.cluster_name.get_project()}.{domain}"
        else:
            fqdn_to_remove = self.fqdn_to_remove

        LOGGER.info("Removing etcd member %s...", fqdn_to_remove)
        remove_node_from_hiera_cookbook = RemoveNodeFromHiera(spicerack=self.spicerack)
        hiera_data = remove_node_from_hiera_cookbook.get_runner(
            args=remove_node_from_hiera_cookbook.argument_parser().parse_args(
                [
                    "--cluster",
                    self.cluster_name.value,
                    "--fqdn-to-remove",
                    fqdn_to_remove,
                ]
            ),
        ).remove_node_from_hiera()
        # Give some time for caches to flush
        time.sleep(30)

        etcd_members = list(sorted(hiera_data["profile::toolforge::k8s::etcd_nodes"], key=natural_sort_key))
        other_etcd_member = etcd_members[0]
        other_etcd_node = remote.query(f"D{{{other_etcd_member}}}", use_sudo=True)
        self.spicerack.etcdctl(remote_host=other_etcd_node).ensure_node_does_not_exist(member_fqdn=fqdn_to_remove)

        if self.skip_etcd_certs_refresh:
            LOGGER.info("Skipping the refresh of all the ssl certs in the cluster (--skip-etcd-certs-refresh)")
        else:
            self._refresh_etcd_certs(etcd_members=etcd_members)

        k8s_control_nodes = get_control_nodes(self.cluster_name)
        _fix_kubeadm(
            remote=remote,
            k8s_control_members=k8s_control_nodes,
            etcd_fqdn_to_remove=fqdn_to_remove,
            etcd_members=etcd_members,
        )

        remove_instance_cookbook = RemoveInstance(spicerack=self.spicerack)
        remove_instance_cookbook.get_runner(
            args=remove_instance_cookbook.argument_parser().parse_args(
                [
                    "--project",
                    self.common_opts.project,
                    "--server-name",
                    fqdn_to_remove.split(".", 1)[0],
                ],
            ),
        ).run()

    def _refresh_etcd_certs(self, etcd_members: list[str]) -> None:
        # refresh the puppet certs with the new alt-name, we use puppet certs
        # for etcd too.
        # TODO: might be interesting to have this as it's own cookbook
        # eventually
        for etcd_member in etcd_members:
            # done one by one to avoid taking the cluster down
            refresh_puppet_certs_cookbook = RefreshPuppetCerts(spicerack=self.spicerack)
            refresh_puppet_certs_cookbook.get_runner(
                args=refresh_puppet_certs_cookbook.argument_parser().parse_args(["--fqdn", etcd_member]),
            ).run()
            # give time for etcd to stabilize
            time.sleep(10)
