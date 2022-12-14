# Wikimedia Cloud Services cookbooks
## Installation
Using your preferred method, install spicerack, the following uses virtualenv and virtualenvwrapper.
From the top of this repository, create a new virtualenv, and install the cookbooks (pulls the dependencies):
```
dcaro@vulcanus$  mkvirtualenv cookbooks
dcaro@vulcanus$  python setup.py install
```

To configure the cookbooks, just run the config generation script from the top of the repo, and follow the instruction:
```
dcaro@vulcanus$ wmcs utils/generate_wmcs_config.sh
```

This will generate the configuration files needed to run the cookbooks directly from this repository.

Now from anywhere , you should be able to run the `cookbook` command (adding something like `-c
~/.config/spicerack/cookbook.yaml` if you did not create the `/etc/spicerack/config.yaml` link).

**NOTE**: make sure you are in the virtualenv we created (`workon cookbooks`).

```
dcaro@vulcanus$ cookbook -l wmcs
cookbooks
`-- wmcs
    |-- wmcs.ceph
    |   |-- wmcs.ceph.osd
    |   |   `-- wmcs.ceph.osd.bootstrap_and_add
    |   |-- wmcs.ceph.reboot_node
...
    |       |-- wmcs.toolforge.worker.depool_and_remove_node
    |       `-- wmcs.toolforge.worker.drain
    `-- wmcs.vps
        |-- wmcs.vps.create_instance_with_prefix
        |-- wmcs.vps.refresh_puppet_certs
        `-- wmcs.vps.remove_instance
```


## Configuration
You can configure the port the socks proxy will use and the path to download the puppet ca by adding a configuration
file under the spicerack config directory named `wmcs.yaml`, with the entry:
```
socks_proxy_port: 12345
puppet_ca_path: ~/.cache/puppet_ca.crt
```


## Some naming rules

All the common used cookbooks for a service (tooforge, vps, ceph, ...) should be at the top level of that service, even
if they are just a proxy for a lower level one.  Individual cookbooks that provide just a functionality or a subset of
a functionality are considered 'library' and should be grouped in a '.lib.' final submodule. Group cookbook packages by
service, then technology, then subcomponent, if they are common through many services, then just technology. Use
meaningful, specific, non-generic names, a verb and if needed a noun (err on the side of extra explicit). Reuse the
keywords explicitly defined, see list below.

Some known services and packages are:
* openstack: lower layer of the infrastructure for the Cloud VPS service.
* vps: operations with Openstack APIs.
* nfs: NFS related stuff.
* toolforge: everything Toolforge.
* toolforge.grid: everything Toolforge grid infrastructure.
* toolforge.k8s: everything Toolforge kubernetes infrastructure.
* toolforge.k8s.etcd: everything Toolforge kubernetes etcd infrastructure.

Some well known keywords:
* ensure: makes sure a condition is met, and acts to fulfill it if not.
* create: every time a cookbook with this keyword runs, a new resource is created.
* remove: every time a cookbook with this keyword runs, a resource is deleted.
* scale: every time a cookbook with this keyword runs, a given service is scaled up (ex. a node is created and pooled).
* downscale: every time a cookbook with this keyword runs, a given service is down-scaled (ex. a node is drained and removed).
* join: used when a resource is configured to be part of a service, cluster or similar. May or may not be pooled.
* pool: start scheduling load in the resource.
* depool: stop scheduling load in the resource, running load might still be running.
* drain: remove any workload running in the resource, and prevent new one from getting scheduled.

A good example:
wmcs.toolforge.scale_grid_exec
wmcs.toolforge.scale_grid_webgen
wmcs.toolforge.scale_grid_weblight

wmcs.toolforge.grid.lib.get_cluster_status
wmcs.toolforge.grid.lib.reconfigure

wmcs.toolforge.grid.node.lib.create_join_pool
wmcs.toolforge.grid.node.lib.join
wmcs.toolforge.grid.node.lib.depool
wmcs.toolforge.grid.node.lib.pool
wmcs.toolforge.grid.node.lib.depool_remove

A bad example:

wmcs.toolforge.scale                          <-- WRONG: scale what?
wmcs.toolforge.reboot                         <-- WRONG: reboot what?
wmcs.toolforge.reboot_node                    <-- WRONG: this should probably be wmcs.toolforge.xxxx.node.lib.reboot
                                                         instead
wmcs.toolforge.grid.lib.add                   <-- WRONG: add what?
wmcs.toolforge.grid.lib.configuration         <-- WRONG: configure what?
wmcs.toolforge.grid.node.lib.create_exec_node <-- WRONG: this should probably be an entry-level cookbook (i.e.
                                                         wmcs.toolforge.create_exec_node)
