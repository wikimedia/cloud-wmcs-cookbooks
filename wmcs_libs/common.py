#!/usr/bin/env python3
"""Cloud Services Cookbooks"""
# pylint: disable=too-many-arguments
from __future__ import annotations

__title__ = __doc__
import argparse
import base64
import json
import logging
import re
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from dataclasses import replace as replace_in_dataclass
from enum import Enum, auto
from functools import partial
from itertools import chain
from typing import Any, Callable, Generator, Pattern
from unittest import mock

import yaml
from ClusterShell.MsgTree import MsgTreeElem
from cumin.transports import Command
from spicerack import ICINGA_DOMAIN, Spicerack
from spicerack.cookbook import CookbookRunnerBase
from spicerack.remote import Remote, RemoteHosts
from wmflib.config import load_yaml_config
from wmflib.irc import SocketHandler

from wmcs_libs.proxy import with_proxy
from wmcs_libs.test_helpers import WMCSCookbookRecorder

LOGGER = logging.getLogger(__name__)
PHABRICATOR_BOT_CONFIG_FILE = "/etc/phabricator_ops-monitoring-bot.conf"
DIGIT_RE = re.compile("([0-9]+)")


def parser_type_list_hostnames(valuelist: list[str]):
    """Validates a datatype in argparser to be a list of hostnames."""
    for value in valuelist:
        parser_type_str_hostname(value)

    return valuelist


def parser_type_str_hostname(value: str):
    """Validates datatype in argparser if a string is a hostname."""
    if "." in value:
        raise argparse.ArgumentTypeError(f"'{value}' contains a dot, likely not a short hostname")

    return value


class ArgparsableEnum(Enum):
    """Enum that behaves well with argparse.

    Example usage:

    class MyEnum(ArgparsableEnum):
        OPT1 = "option 1"
        OPT2 = "option 2"

    parser.add_argument(
        "--my-enum",
        choices=list(MyEnum),
        type=MyEnum,
        default=MyEnum.OPT1,
    )
    """

    def __str__(self):
        """Needed to show the nice string values and for argparse to use those to call the `type` parameter."""
        return self.value


class OutputFormat(Enum):
    """Types of format supported to try to decode when running commands."""

    JSON = auto()
    YAML = auto()


@dataclass(frozen=True)
class CuminParams:
    """Bundle of the parameters that run_sync allows."""

    print_output: bool = True
    print_progress_bars: bool = True
    is_safe: bool = False
    success_threshold: float = 1.0
    batch_size: int | str | None = None
    batch_sleep: float | None = None

    @staticmethod
    def replace(original: "CuminParams" | None, **what: Any) -> "CuminParams":
        """Given CuminParams instance, create a new one based on the existing one with the given params replaced.

        The staticmethod is needed as we sometimes get None instead of a CuminParams instance.
        """
        if not original:
            return CuminParams(**what)

        return replace_in_dataclass(original, **what)

    @staticmethod
    def as_safe(original: "CuminParams" | None) -> "CuminParams":
        """Return this same params but with the safe flag on.

        The staticmethod is needed as we sometimes get None instead of a CuminParams instance.
        """
        return CuminParams.replace(original=original, is_safe=True)


# Handy pre-set common cumin params
CUMIN_SAFE_WITHOUT_OUTPUT = CuminParams(print_output=False, print_progress_bars=False, is_safe=True)
CUMIN_UNSAFE_WITHOUT_OUTPUT = CuminParams(print_output=False, print_progress_bars=False)
CUMIN_SAFE_WITH_OUTPUT = CuminParams(is_safe=True)
CUMIN_UNSAFE_WITH_OUTPUT = CuminParams()


@dataclass(frozen=True)
class CommonOpts:
    """Common WMCS cookbook options."""

    project: str = "admin"
    task_id: str | None = None
    no_dologmsg: bool = False
    http_proxy: str | None = None

    def to_cli_args(self) -> list[str]:
        """Helper to unwrap the options for use with argument parsers."""
        args = []
        args.extend(["--project", self.project])

        if self.task_id:
            args.extend(["--task-id", self.task_id])
        if self.no_dologmsg:
            args.extend(["--no-dologmsg"])

        return args


def add_common_opts(parser: argparse.ArgumentParser, project_default: str | None = "admin") -> argparse.ArgumentParser:
    """Adds the common WMCS options to a cookbook parser."""
    if project_default is not None:
        parser.add_argument(
            "--project",
            default=project_default,
            help="Relevant Cloud VPS openstack project (for operations, dologmsg, etc). "
            "If this cookbook is for hardware, this only affects dologmsg calls. "
            "Default is '%(default)s'.",
        )

    parser.add_argument(
        "--task-id",
        required=False,
        default=None,
        help="Id of the task related to this operation (ex. T123456).",
    )
    parser.add_argument(
        "--no-dologmsg",
        required=False,
        action="store_true",
        help="To disable dologmsg calls (no SAL messages on IRC).",
    )

    return parser


def with_common_opts(spicerack: Spicerack, args: argparse.Namespace, runner: Callable) -> Callable:
    """Helper to add CommonOpts to a cookbook instantiation."""
    no_dologmsg = bool(spicerack.dry_run or args.no_dologmsg)
    # when a proxy is not set, spicerack.http_proxy returns an empty string
    http_proxy = spicerack.http_proxy if spicerack.http_proxy else None

    common_opts = CommonOpts(project=args.project, task_id=args.task_id, no_dologmsg=no_dologmsg, http_proxy=http_proxy)

    return partial(runner, common_opts=common_opts)


def run_one_raw_needed_to_be_able_to_mock(
    command: list[str] | Command,
    node: RemoteHosts,
    capture_errors: bool = False,
    last_line_only: bool = False,
    skip_first_line: bool = False,
    cumin_params: None | CuminParams = None,
) -> str:
    """Only exists to be able to mock in one single place the run_one_raw function.

    Useful when testing and/or recording test cases. Don't use unless you know what you are sure, use run_one_raw
    instead for most cases.
    """
    if not isinstance(command, Command):
        command = Command(command=" ".join(command), ok_codes=[] if capture_errors else [0])

    run_sync_params = asdict(cumin_params) if cumin_params else {}

    try:
        result = next(node.run_sync(command, **run_sync_params))

    except StopIteration:
        return ""

    message = raw_result = result[1].message()
    # Avoid crashing if we can't decode properly
    raw_result = message.decode("utf-8", "backslashreplace")

    if skip_first_line:
        raw_result = "\n".join(raw_result.splitlines()[1:])

    if last_line_only:
        raw_result = raw_result.splitlines()[-1]

    return raw_result


def run_one_raw(
    command: list[str] | Command,
    node: RemoteHosts,
    capture_errors: bool = False,
    last_line_only: bool = False,
    skip_first_line: bool = False,
    cumin_params: CuminParams | None = None,
) -> str:
    """Run a command on a node.

    Returns the the raw output.
    """
    return run_one_raw_needed_to_be_able_to_mock(
        command=command,
        node=node,
        capture_errors=capture_errors,
        last_line_only=last_line_only,
        skip_first_line=skip_first_line,
        cumin_params=cumin_params,
    )


def run_one_formatted_as_list(
    command: list[str] | Command,
    node: RemoteHosts,
    capture_errors: bool = False,
    last_line_only: bool = False,
    skip_first_line: bool = False,
    try_format: OutputFormat = OutputFormat.JSON,
    cumin_params: CuminParams | None = None,
) -> list[Any]:
    """Run one command and return a list of elements."""
    result = run_one_formatted(
        command=command,
        node=node,
        capture_errors=capture_errors,
        last_line_only=last_line_only,
        skip_first_line=skip_first_line,
        try_format=try_format,
        cumin_params=cumin_params,
    )
    if not isinstance(result, list):
        raise TypeError(f"Was expecting a list, got {result}")

    return result


def run_one_as_dict(
    command: list[str] | Command,
    node: RemoteHosts,
    capture_errors: bool = False,
    last_line_only: bool = False,
    skip_first_line: bool = False,
    try_format: OutputFormat = OutputFormat.JSON,
    cumin_params: CuminParams | None = None,
) -> dict[str, Any]:
    """Run a command and return a dict."""
    result = run_one_formatted(
        command=command,
        node=node,
        capture_errors=capture_errors,
        last_line_only=last_line_only,
        skip_first_line=skip_first_line,
        try_format=try_format,
        cumin_params=cumin_params,
    )
    if not isinstance(result, dict):
        raise TypeError(f"Was expecting a dict, got {result}")

    return result


def run_one_formatted(
    command: list[str] | Command,
    node: RemoteHosts,
    capture_errors: bool = False,
    last_line_only: bool = False,
    skip_first_line: bool = False,
    ignore_lines: list[Pattern[str]] | None = None,
    try_format: OutputFormat = OutputFormat.JSON,
    cumin_params: CuminParams | None = None,
) -> list[Any] | dict[str, Any]:
    """Run a command on a node.

    Returns the loaded json/yaml.
    """
    raw_result = run_one_raw(
        command=command,
        node=node,
        capture_errors=capture_errors,
        last_line_only=last_line_only,
        skip_first_line=skip_first_line,
        cumin_params=cumin_params,
    )

    if ignore_lines:
        raw_result = "\n".join(
            line for line in raw_result.splitlines() if not any(pattern.match(line) for pattern in ignore_lines)
        )

    try:
        if try_format == OutputFormat.JSON:
            return json.loads(raw_result)

        if try_format == OutputFormat.YAML:
            return yaml.safe_load(raw_result)

    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise Exception(f"Unable to parse output of command as {try_format}:\n{raw_result}") from error

    raise Exception(f"Unrecognized format {try_format}")


def simple_create_file(
    dst_node: RemoteHosts,
    contents: str,
    remote_path: str,
    use_root: bool = True,
    cumin_params: CuminParams | None = None,
) -> None:
    """Creates a file on the remote host/hosts with the given content."""
    # this makes it easier to get away with quotes or similar
    base64_content = base64.b64encode(contents.encode("utf8"))
    full_command = ["echo", f"'{base64_content.decode()}'", "|", "base64", "--decode", "|"]
    if use_root:
        full_command.extend(["sudo", "-i"])

    full_command.extend(["tee", remote_path])

    run_one_raw(node=dst_node, command=full_command, cumin_params=cumin_params)


@contextmanager
def with_temporary_file(
    dst_node: RemoteHosts, contents: str, use_root: bool = True, cumin_params: CuminParams | None = None
) -> Generator[str, None, None]:
    """Context manager to do something with on a remote system with a temporary file."""
    file_path = f"/tmp/{str(uuid.uuid4())}"  # nosec B108

    try:
        simple_create_file(
            dst_node=dst_node, contents=contents, remote_path=file_path, use_root=use_root, cumin_params=cumin_params
        )

        yield file_path
    finally:
        run_one_raw(node=dst_node, command=["rm", "-v", file_path], cumin_params=cumin_params)


def natural_sort_key(element: str) -> list[str | int]:
    """Changes "name-12.something.com" into ["name-", 12, ".something.com"]."""
    return [int(mychunk) if mychunk.isdigit() else mychunk for mychunk in DIGIT_RE.split(element)]


def wrap_with_sudo_icinga(my_spicerack: Spicerack) -> Spicerack:
    """Wrap spicerack icinga to allow sudo.

    We have to patch the master host to allow sudo, all this weirdness is
    because icinga_master_host is a @property and can't be patched on
    the original instance
    """

    class SudoIcingaSpicerackWrapper(Spicerack):
        """Dummy wrapper class to allow sudo icinga."""

        def __init__(self):  # pylint: disable-msg=super-init-not-called
            """Init."""

        @property
        def icinga_master_host(self) -> RemoteHosts:
            """Icinga master host."""
            new_host = self.remote().query(query_string=self.dns().resolve_cname(ICINGA_DOMAIN), use_sudo=True)
            return new_host

        def __getattr__(self, what):
            return getattr(my_spicerack, what)

        def __setattr__(self, what, value):
            return setattr(my_spicerack, what, value)

    return SudoIcingaSpicerackWrapper()


@dataclass(frozen=True)
class SALLogger:
    """Deprecated, use spicerack.sal_logger instead."""

    @classmethod
    def from_common_opts(cls, common_opts: CommonOpts, project: str | None = None) -> "SALLogger":
        """Get a SALLogger from some CommonOpts."""
        # pylint: disable=unused-argument
        return cls()

    def log(
        self,
        message: str,
    ):
        """Log a message to the given irc channel for stashbot to pick up and register in SAL."""
        logging.getLogger("spicerack_sal_logger").info("%s", message)


# Poor man's namespace to compensate for the restriction to not create modules
@dataclass(frozen=True)
class UtilsForTesting:
    """Generic testing utilities."""

    @staticmethod
    def to_parametrize(test_cases: dict[str, dict[str, Any]]) -> dict[str, str | list[Any]]:
        """Helper for parametrized tests.

        Use like:
        @pytest.mark.parametrize(**_to_parametrize(
            {
                "Test case 1": {"param1": "value1", "param2": "value2"},
                # will set the value of the missing params as `None`
                "Test case 2": {"param1": "value1"},
                ...
            }
        ))
        """
        _param_names = set(chain(*[list(params.keys()) for params in test_cases.values()]))

        def _fill_up_params(test_case_params):
            # {
            #    'key': value,
            #    'key2': value2,
            # }
            end_params = []
            for must_param in _param_names:
                end_params.append(test_case_params.get(must_param, None))

            return end_params

        if len(_param_names) == 1:
            argvalues = [_fill_up_params(test_case_params)[0] for test_case_params in test_cases.values()]

        else:
            argvalues = [_fill_up_params(test_case_params) for test_case_params in test_cases.values()]

        return {"argnames": ",".join(_param_names), "argvalues": argvalues, "ids": list(test_cases.keys())}

    @staticmethod
    def get_fake_remote_hosts(
        responses: list[str] | None = None, side_effect: list[Any] | None = None
    ) -> mock.MagicMock:
        """Create a fake RemoteHosts object.

        It will return a RemoteHosts that will return the given responses when run_sync is called in them.
        If side_effect is passed, it will override the responses and set that as side_effect of the mock on run_sync.
        """
        responses = responses if responses is not None else []
        fake_hosts = mock.create_autospec(spec=RemoteHosts, spec_set=True)

        def _get_fake_msg_tree(msg_tree_response: str):
            fake_msg_tree = mock.create_autospec(spec=MsgTreeElem, spec_set=True)
            fake_msg_tree.message.return_value = msg_tree_response.encode()
            return fake_msg_tree

        if side_effect is not None:
            fake_hosts.run_sync.side_effect = side_effect
        else:
            # the return type of run_sync is Iterator[Tuple[NodeSet, MsgTreeElem]]
            fake_hosts.run_sync.return_value = (
                (None, _get_fake_msg_tree(msg_tree_response=response)) for response in responses
            )

        return fake_hosts

    @staticmethod
    def get_fake_remote(responses: list[str] | None = None, side_effect: list[Any] | None = None) -> mock.MagicMock:
        """Create a fake remote.

        It will return a RemoteHosts that will return the given responses when run_sync is called in them.
        If side_effect is passed, it will override the responses and set that as side_effect of the mock on run_sync.
        """
        fake_hosts = UtilsForTesting.get_fake_remote_hosts(responses=responses, side_effect=side_effect)
        fake_remote = mock.create_autospec(spec=Remote, spec_set=True)

        fake_remote.query.return_value = fake_hosts

        return fake_remote

    @staticmethod
    def get_fake_spicerack(fake_remote: mock.MagicMock) -> mock.MagicMock:
        """Create a fake spicerack."""
        fake_spicerack = mock.create_autospec(spec=Spicerack)
        fake_spicerack.remote.return_value = fake_remote
        return fake_spicerack


class CmdChecklistParsingError(Exception):
    """CmdChecklistParsingError used to signal that we failed to parse cmd-checklist-runner output."""


@dataclass(frozen=True)
class CmdChecklistResults:
    """CmdChecklistResults to host the results of running cmd-checklist-runner."""

    passed: int
    failed: int
    total: int


class CmdChecklist:
    """CmdChecklist to abstract running cmd-checklist-runner on a remote host."""

    def __init__(self, name: str, remote_hosts: RemoteHosts, config_file: str):
        """Init."""
        self.name = name
        self.remote_hosts = remote_hosts
        self.config_file = config_file

    def _parse_output(self, output_lines: list[str]) -> CmdChecklistResults:
        """Parse output from cmd-checklist-runner."""
        passed = failed = total = -1

        for line in output_lines:
            if " INFO: --- passed tests: " in line:
                passed = int(line.split(" ")[-1])
                continue

            if " INFO: --- failed tests: " in line:
                failed = int(line.split(" ")[-1])
                continue

            if " INFO: --- total tests: " in line:
                total = int(line.split(" ")[-1])
                continue

        if passed < 0 or failed < 0 or total < 0:
            raise CmdChecklistParsingError(f"{self.name}: unable to parse the output from cmd-checklist-runner")

        return CmdChecklistResults(passed=passed, failed=failed, total=total)

    def run(self, cumin_params: CuminParams | None = None) -> CmdChecklistResults:
        """Run the cmd-checklist-runner testsuite."""
        # Not sure if this is what we want, it's what was there
        final_cumin_params = CuminParams.as_safe(cumin_params)
        output_lines = run_one_raw(
            node=self.remote_hosts,
            command=["cmd-checklist-runner", "--config", self.config_file],
            cumin_params=final_cumin_params,
        ).splitlines()

        return self._parse_output(output_lines)

    def evaluate(self, results: CmdChecklistResults) -> int:
        """Evaluate the cmd-checklist-runner results."""
        if results.total < 1:
            LOGGER.warning("%s: no tests were run!", self.name)
            return 0

        if results.failed > 0:
            LOGGER.error("%s: %s failed tests detected!", self.name, results.failed)
            return 1

        LOGGER.info("%s: %s/%s passed tests.", self.name, results.passed, results.total)
        return 0


class CommandRunnerMixin:
    """Mixin to get command running functions."""

    def __init__(self, command_runner_node: RemoteHosts):
        """Simple mixin to provide command running functions to a class."""
        self.command_runner_node = command_runner_node

    def _get_full_command(
        self, *command: str, json_output: bool = True, project_as_arg: bool = False, with_env_var: bool = True
    ):
        raise NotImplementedError

    def run_raw(
        self,
        *command: str,
        capture_errors: bool = False,
        json_output=True,
        project_as_arg: bool = False,
        with_env_var: bool = True,
        cumin_params: CuminParams | None = None,
    ) -> str:
        """Run a command on a runner node.

        Returns the raw output (not loaded from json).
        """
        full_command = self._get_full_command(
            *command, json_output=json_output, project_as_arg=project_as_arg, with_env_var=with_env_var
        )
        return run_one_raw(
            command=full_command,
            node=self.command_runner_node,
            capture_errors=capture_errors,
            cumin_params=cumin_params,
        )

    def run_formatted_as_dict(
        self,
        *command: str,
        capture_errors: bool = False,
        project_as_arg: bool = False,
        cumin_params: CuminParams | None = None,
        try_format: OutputFormat = OutputFormat.JSON,
        last_line_only: bool = False,
        with_env_var: bool = True,
        skip_first_line: bool = False,
    ) -> dict[str, Any]:
        """Run a command on a runner node forcing json output.

        Returns a dict with the formatted output (loaded from json), usually for show commands.

        Example:
            >>> self.run_formatted("port", "show")
            {
                "admin_state_up": true,
                "allowed_address_pairs": [],
                ...
                "status": "ACTIVE",
                "tags": [],
                "trunk_details": null,
                "updated_at": "2022-04-21T05:18:43Z"
            }

        """
        full_command = self._get_full_command(
            *command, json_output=True, project_as_arg=project_as_arg, with_env_var=with_env_var
        )
        return run_one_as_dict(
            command=full_command,
            node=self.command_runner_node,
            capture_errors=capture_errors,
            try_format=try_format,
            cumin_params=cumin_params,
            skip_first_line=skip_first_line,
            last_line_only=last_line_only,
        )

    def run_formatted_as_list(
        self,
        *command: str,
        capture_errors: bool = False,
        project_as_arg: bool = False,
        skip_first_line: bool = False,
        with_env_var: bool = True,
        cumin_params: CuminParams | None = None,
    ) -> list[Any]:
        """Run an command on a runner node forcing json output.

        Returns a list with the formatted output (loaded from json), usually for `list` commands.

        Example:
            >>> self.run_formatted_as_list("port", "list")
            [
                {
                    "ID": "fb751dd4-05bb-4f23-822f-852f55591a11",
                    "Name": "",
                    "MAC Address": "fa:16:3e:25:48:ca",
                    "Fixed IP Addresses": [
                        {
                            "subnet_id": "7adfcebe-b3d0-4315-92fe-e8365cc80668",
                            "ip_address": "172.16.128.110"
                        }
                    ],
                    "Status": "ACTIVE"
                },
                {
                    "ID": "fb9a2e11-39af-4fa2-80a7-5f895d42b68a",
                    "Name": "",
                    "MAC Address": "fa:16:3e:7f:80:e8",
                    "Fixed IP Addresses": [
                        {
                            "subnet_id": "7adfcebe-b3d0-4315-92fe-e8365cc80668",
                            "ip_address": "172.16.128.115"
                        }
                    ],
                    "Status": "DOWN"
                },
            ]

        """
        full_command = self._get_full_command(
            *command, json_output=True, project_as_arg=project_as_arg, with_env_var=with_env_var
        )
        return run_one_formatted_as_list(
            command=full_command,
            node=self.command_runner_node,
            capture_errors=capture_errors,
            skip_first_line=skip_first_line,
            cumin_params=cumin_params,
        )


class WMCSCookbookRunnerBase(CookbookRunnerBase):
    """WMCS tweaks to the base cookbook runner.

    Current tweaks:
    * Start and stop a socks proxy when running the cookbook:
      Define the `run_with_proxy` method instead of the `run` method when writing your cookbook.
    """

    recorder: WMCSCookbookRecorder | None = None

    def __init__(self, spicerack: Spicerack, common_opts: CommonOpts):
        """Init"""
        self.spicerack = spicerack
        self._setup_logging(common_opts)
        self.nested = bool(WMCSCookbookRunnerBase.recorder)
        self.wmcs_config = self._load_config()
        LOGGER.debug("Starting %s recorder", "nested" if self.nested else "not nested")
        if not self.nested:
            WMCSCookbookRunnerBase.recorder = WMCSCookbookRecorder()

    def _load_config(self) -> dict[str, Any]:
        wmcs_config_path = self.spicerack.config_dir / "wmcs.yaml"
        if not wmcs_config_path.exists():
            LOGGER.debug("No WMCS config found on %s. Continuing...", wmcs_config_path)
            return {}

        LOGGER.info("Loading WMCS config from %s", wmcs_config_path)
        return load_yaml_config(config_file=wmcs_config_path, raises=False)

    def _setup_logging(self, common_opts: CommonOpts):
        if common_opts.no_dologmsg:
            self.spicerack.sal_logger.handlers.clear()
            return

        # Note: we want to send a message to IRC that looks like
        #     !log user@host project message (task_id)
        # We add "project" and "task_id" here, whereas "!log user@host" is added by
        # https://gerrit.wikimedia.org/r/plugins/gitiles/operations/software/pywmflib/+/refs/heads/master/wmflib/irc.py#73

        task_id = f" ({common_opts.task_id})" if common_opts.task_id else ""
        wmcs_formatter = logging.Formatter(f"{common_opts.project} %(message)s{task_id}")

        if self.spicerack.sal_logger.handlers:
            # If using the Spicerack configured logger (tcpircbot),
            # we just need to add the project name.
            self.spicerack.sal_logger.handlers[0].setFormatter(wmcs_formatter)
        else:
            # When running cookbooks from a laptop, we cannot reach tcpircbot,
            # so we use wm-bot instead, which has a similar syntax but requires
            # you to prepend the IRC channel name before "!log"
            # TODO: Update Spicerack to allow configuring wm-bot usage properly on local setups,
            # so we don't need this kind of set up that hacks the channel in front.
            handler = SocketHandler("wm-bot.wm-bot.wmcloud.org", 64835, self.spicerack.username)
            handler.command = "#wikimedia-cloud-feed !log"
            handler.setFormatter(wmcs_formatter)
            self.spicerack.sal_logger.addHandler(handler)

    def __getattribute__(self, __name: str) -> Any:
        """Needed to be able to save the recordings if needed as the run function might get overwritten."""
        if __name == "run":

            def _wrapped_run(*args, **kwargs):
                try:
                    return object.__getattribute__(self, __name)(*args, **kwargs)
                finally:
                    if not self.nested:
                        LOGGER.debug("Cleaning up recorder.")
                        recorder = WMCSCookbookRunnerBase.recorder
                        # cleanup the old recorder even if save or check_missed_record_entries raises
                        # otherwise consecutive runs on pytest will start with the old recorder
                        WMCSCookbookRunnerBase.recorder = None
                        recorder.save()
                        recorder.check_missed_record_entries()

            return _wrapped_run

        return super().__getattribute__(__name)

    def run(self) -> int | None:
        """Main entry point"""
        with with_proxy(spicerack=self.spicerack, config=self.wmcs_config):
            return self.run_with_proxy()

    def run_with_proxy(self) -> int | None:
        """Main entry point, use in place of `run` to execute it's code with a socks proxy running."""
        return 0
