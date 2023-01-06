#!/usr/bin/env python3
"""Cloud Services Cookbooks"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

LOGGER = logging.getLogger(__name__)


class TestHelperError(Exception):
    """Generic test helper exception."""


class ReplayError(TestHelperError):
    """An error happened when replaying some recording."""


class RecordError(TestHelperError):
    """An error happened when recording some cookbook run."""


@dataclass
class Recording:
    """The data we recorded for an interception."""

    params: dict[str, Any]
    output: Any
    # -1 for continuously repeating
    repeat_num: int = 1


class WMCSCookbookRecorder:
    """Class to handle recordings and after replay them for testing."""

    def __init__(self) -> None:
        """Init function."""
        self.recording = bool(os.environ.get("COOKBOOK_RECORDING_ENABLED"))
        self.replaying = bool(os.environ.get("COOKBOOK_REPLAYING_ENABLED"))
        self.next_entry = 0
        self.recordings_file = Path(os.environ.get("COOKBOOK_RECORDING_FILE", ""))
        self.recordings: list[Recording] = []
        self.patcher = mock.patch("wmcs_libs.common.run_one_raw_needed_to_be_able_to_mock", self)

        # importing here to avoid import loops
        # pylint: disable-msg=import-outside-toplevel,cyclic-import
        from wmcs_libs.common import run_one_raw_needed_to_be_able_to_mock

        self.original = run_one_raw_needed_to_be_able_to_mock

        if self.recording and self.replaying:
            raise TestHelperError("You can't replay and record at the same time, use only one.")

        if self.recording:
            self.init_record()
        elif self.replaying:
            self.init_replay()

    def check_missed_record_entries(self):
        """Raises ReplayError if we did not play all the entries in the record."""
        if not self.replaying:
            return

        we_are_in_last_entry = self.next_entry == (len(self.recordings) - 1)
        last_entry_is_infinite = self.recordings[-1].repeat_num < 0

        if self.next_entry < len(self.recordings) and not (we_are_in_last_entry and last_entry_is_infinite):
            raise ReplayError(
                f"Not all the entries in the record {self.recordings_file} were replayed, only {self.next_entry - 1} "
                "were."
            )

    def init_record(self) -> None:
        """Initialize/check that everything is ready for a recording."""
        if "COOKBOOK_RECORDING_FILE" not in os.environ:
            raise RecordError("You must specify a recording file with the env var COOKBOOK_RECORDING_FILE")

        self.patcher.start()

    def init_replay(self) -> None:
        """Initialize/check that everything is ready for a relpay."""
        if "COOKBOOK_RECORDING_FILE" not in os.environ:
            raise ReplayError("You must specify a recording file with the env var COOKBOOK_RECORDING_FILE")

        self.patcher.start()
        self.recordings = [Recording(**params) for params in yaml.safe_load_all(self.recordings_file.read_bytes())]

    @staticmethod
    def is_serializable(what: Any) -> bool:
        """Check if the given object can be serialized with yaml without extra support."""
        if isinstance(what, (str, int, bool, bytes)) or what is None:
            return True

        if isinstance(what, (list, tuple, dict)):
            return all(WMCSCookbookRecorder.is_serializable(elem) for elem in what)

        return False

    def __call__(self, *args, **kwargs) -> Any:
        """Called instead of the patched function."""
        params = {
            "args": [arg if WMCSCookbookRecorder.is_serializable(arg) else "non-serializable" for arg in args],
            "kwargs": {
                key: val if WMCSCookbookRecorder.is_serializable(val) else "non-serializable"
                for key, val in kwargs.items()
            },
        }

        if self.recording:
            output = self.original(*args, **kwargs)

            self.recordings.append(
                Recording(
                    params=params,
                    output=output if WMCSCookbookRecorder.is_serializable(output) else "non-serializable",
                )
            )

        elif self.replaying:
            # TODO: check also if the parameters match the recording
            if self.next_entry >= len(self.recordings):
                raise ReplayError(
                    "Got more calls than found in the recording... (requested call:"
                    f"{self.next_entry}, recoding length:{len(self.recordings)}"
                )

            cur_entry = self.recordings[self.next_entry]
            LOGGER.debug(
                "Replaying entry %d (repeats left %s) from %s",
                self.next_entry,
                str(cur_entry.repeat_num - 1) if cur_entry.repeat_num >= 0 else "infinite",
                self.recordings_file,
            )
            output = self.recordings[self.next_entry].output
            if cur_entry.repeat_num > 0:
                cur_entry.repeat_num -= 1
            if cur_entry.repeat_num == 0:
                self.next_entry += 1

        return output

    def save(self) -> None:
        """Save the current recording if we are in recording mode."""
        if not self.recording:
            return

        os.makedirs(self.recordings_file.parent, exist_ok=True)
        self.recordings_file.write_text(
            yaml.safe_dump_all([asdict(recording) for recording in self.recordings]), encoding="utf-8"
        )
