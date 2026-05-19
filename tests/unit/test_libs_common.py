from __future__ import annotations

import socket
from argparse import ArgumentTypeError
from typing import Optional

import pytest

from wmcs_libs.common import (
    UtilsForTesting,
    get_ip_address_family,
    validate_v_version,
    validate_version,
)


@pytest.mark.parametrize(
    **UtilsForTesting.to_parametrize(
        test_cases={
            "v4": {
                "address": "1.2.3.4",
                "af": socket.AF_INET,
            },
            "v6": {
                "address": "::1",
                "af": socket.AF_INET6,
            },
            "invalid": {
                "address": "foo",
                "af": None,
            },
        }
    )
)
def test_get_ip_address_family_basic(address: str, af: Optional[int]):
    result = get_ip_address_family(address)
    assert result == af


def test_validate_version_ok():
    assert validate_version("1.23.4") == "1.23.4"
    assert validate_version(" 1.23. 4  ") == "1.23.4"


@pytest.mark.parametrize("version", ["", "aaaa", "1.23", "1.23.4.5", "1.23.", "1..2", "aa.aa.aa", "v1.23.4"])
def test_validate_version_error(version):
    with pytest.raises(ArgumentTypeError) as exc:
        assert validate_version(version) is None
    assert exc.value.args[0] == f"Expected version in minor.major.patch format, got '{version}'"


def test_validate_v_version_ok():
    assert validate_v_version("v1.23.4") == "v1.23.4"


@pytest.mark.parametrize("version", ["", "aaa", "vaaaa", "1.23.4", "v1.2.3.4.5"])
def test_validate_v_version_error(version):
    with pytest.raises(ArgumentTypeError):
        assert validate_v_version(version) is None
