from __future__ import annotations

import socket
from typing import Optional

import pytest

from wmcs_libs.common import UtilsForTesting, get_ip_address_family


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
