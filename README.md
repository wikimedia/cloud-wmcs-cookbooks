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
    |       |-- wmcs.toolforge.k8s.worker.depool_and_remove_node
    |       `-- wmcs.toolforge.k8s.worker.drain
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

## Recorded test cases/functional tests

### Recording a run
**NOTE**: Currently it will only record calls to `wmcs_libs.common.run_*` functions, not spicerack/http requests/etc., so for now **avoid using those directly in your cookbooks!**

To record a run, you can just set the environment variable `COOKBOOK_RECORDING_ENABLED` to `true` and specify an output file with `COOKBOOK_RECORDING_FILE=/path/to/my/file.yaml` when running your cookbook, for example:

```
COOKBOOK_RECORDING_FILE=record.yaml COOKBOOK_RECORDING_ENABLED=true cookbook wmcs.ceph.osd.show_info --cluster-name codfw1
```

That will create a file under `./record.yaml` with the recorded contents of the run:
```
dcaro@vulcanus$ file record.yaml 
record.yaml: ASCII text, with very long lines (1538)
```

**NOTE**: When recording, the cookook **does run as usual** so it will actually make changes, take down nodes and any other action the cookbook would normally do.

### Manually replaying a run

You would not usually want to reply a run outside of a test file, but you can, you can set the env var `COOKBOOK_REPLAYING_ENABLED=true` and point `COOKBOOK_RECORDING_FILE=/path/to/my/recording.yaml`:

```
COOKBOOK_RECORDING_FILE=record.yaml COOKBOOK_REPLAYING_ENABLED=true cookbook wmcs.ceph.osd.show_info --cluster-name codfw1
```

**NOTE**: This will not actually run any commands on the remote hosts, instead will return the recorded response.


### Manually changing the recording
Sometimes it might be too difficult to record all the edge cases for a cookbook run, as a fallback you can manually edit the recording to match the test case you want to check.

The recording file is just a yaml with one hash per recorded call, these calls will be returned one after the other starting from the first of the file.

These are some of the interesting keys in each call recording:
#### output
This stores the string with the output from running the command, you can change this to match your need though it's highly recommended to use real outputs.

#### repeat_num
This is the number of times this recorded call will be returned before continuing to the next. By default is 1, but you can use a different number to reproduce scenarios where the state does not change, or you need to test retries.

A custom value of `-1` will make the replay return this call forever and never get to the next.


### params
This is the list of captured args and kwargs passed to the mocked function (currently `run_one_raw`), not yet used for anything but to inform you about the command that was being executed.

## Using the replays in a test case
Once you have a recording, you can put it at a folder called `recordings` at the same level as your test file (under any subdirectory in `tests/functional`), and use the `run_cookbook_with_recording` fixture to load it at test time and run a cookbook (no need to import it):

```
def test_everything_goes_as_planned(run_cookbook_with_recording):
    result = run_cookbook_with_recording(
        record_file_name="my_recording.yaml",
        argv=["my.cookbook", "--param-1=value1", ...],
    )

    assert result.return_code == 0
    assert "Got an error" not in result.stderr
    assert "This happened" in result.stdout
```

More details in the fixture docs (under `tests/functional/conftest.py`).
