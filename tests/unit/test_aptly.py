from wmcs_libs.aptly import Aptly
from wmcs_libs.common import UtilsForTesting


def test_Aptly_get_repositories():
    fake_remote = UtilsForTesting.get_fake_remote_hosts(
        responses=["bookworm-tools\nbookworm-toolsbeta\nbullseye-tools\nbullseye-toolsbeta\n"]
    )

    aptly = Aptly(fake_remote)
    assert aptly.get_repositories() == ["bookworm-tools", "bookworm-toolsbeta", "bullseye-tools", "bullseye-toolsbeta"]
