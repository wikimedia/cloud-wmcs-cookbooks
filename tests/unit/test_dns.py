from wmcs_libs.dns import format_srv_record


def test_format_srv_record():
    assert format_srv_record(priority=1, weight=2, port=443, target="example.org.") == "1 2 443 example.org."
