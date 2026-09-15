from spicerack import Remote

from wmcs_libs.dns import format_srv_record
from wmcs_libs.etcd.clusters import get_cluster_dns_records
from wmcs_libs.inventory.etcd import EtcdClusterName
from wmcs_libs.openstack.common import OpenstackAPI


def update_etcd_cluster_dns_records(
    remote: Remote,
    cluster_name: EtcdClusterName,
    cluster_members: list[str],
) -> None:
    for record in get_cluster_dns_records(cluster_name):
        openstack_api = OpenstackAPI(
            remote=remote,
            cluster_name=cluster_name.get_openstack_cluster_name(),
            project=record.zone_project_id,
        )

        record_content = [
            format_srv_record(
                priority=1,
                weight=1,
                port=record.port,
                target=f"{member}.",
            )
            for member in cluster_members
        ]

        records = openstack_api.recordset_get(
            record.zone_id,
            "SRV",
            record.record_name,
        )
        if records:
            openstack_api.recordset_update(
                record.zone_id,
                records[0]["id"],
                *record_content,
            )
            return
        openstack_api.recordset_create(
            record.zone_id,
            "SRV",
            record.record_name,
            *record_content,
        )
