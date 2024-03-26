"""Base classes for implementing cookbooks that run batch operations on server clusters."""
from __future__ import annotations

from abc import ABCMeta, abstractmethod
from datetime import timedelta

from spicerack import RemoteHosts, Spicerack

from wmcs_libs.common import CommonOpts, WMCSCookbookRunnerBase


class WMCSCookbookBatchRunnerBase(WMCSCookbookRunnerBase, metaclass=ABCMeta):
    """Base cookbook runner class for batch operations server clusters."""

    downtime_reason: str | None = None
    """If set to a string, the hosts being operated will be downtimed with that reason."""

    downtime_duration: timedelta = timedelta(minutes=30)
    """Duration to downtime hosts for."""

    def __init__(
        self,
        common_opts: CommonOpts,
        spicerack: Spicerack,
    ):
        """Init"""
        self.common_opts = common_opts
        super().__init__(spicerack=spicerack, common_opts=common_opts)
        self.query: str | None = None

    @property
    def runtime_description(self) -> str:
        """Return a nicely formatted string that represents the cookbook action."""
        if not self.query:
            raise NotImplementedError("Subclass did not set self.query in constructor")
        return f"on hosts matched by '{self.query}'"

    def run_batch_operation(self) -> int | None:
        if not self.query:
            raise NotImplementedError("Subclass did not set self.query in constructor")
        result = self.spicerack.remote().query(self.query, use_sudo=True)

        # TODO: make batch size configurable
        for hosts in result.split(len(result)):
            am_hosts = None
            downtime_id = None
            if self.downtime_reason:
                am_hosts = self.spicerack.alertmanager_hosts(hosts.hosts)
                downtime_id = am_hosts.downtime(
                    reason=self.spicerack.admin_reason(self.downtime_reason, self.common_opts.task_id),
                    duration=self.downtime_duration,
                )

            self.run_on_hosts(hosts)

            if am_hosts and downtime_id:
                am_hosts.remove_downtime(downtime_id)

        return 0

    def run_with_proxy(self) -> int | None:
        # With proxy for PuppetDB access.
        return self.run_batch_operation()

    @abstractmethod
    def run_on_hosts(self, hosts: RemoteHosts) -> None:
        """Run the operation on the given set of hosts."""
