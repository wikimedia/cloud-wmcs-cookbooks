"""Package configuration."""

from setuptools import find_namespace_packages, find_packages, setup

# The below list is only for CI
# For prod add the libs to modules/profile/manifests/spicerack.pp
install_requires = [
    "pyyaml",
    "wikimedia-spicerack>=10.0.0",
    "requests[socks]",
    "python-gitlab==2.5.0",
]

# Extra dependencies
extras_require = {
    # Test dependencies
    "tests": [
        "bandit>=1.5.0",
        "flake8>=3.2.1",
        "pytest>=6.1.0",
        "freezegun>=1.2.2",
    ],
    "prospector": [
        "prospector[with_everything]>=0.12.4,<=1.7.7",
        "pylint<2.15.7",  # Temporary upper limit for an upstream regression
        "pytest>=6.1.0",
    ],
}

setup_requires = [
    "setuptools_scm>=1.15.0",
]

setup(
    author="Riccardo Coccioli",
    author_email="rcoccioli@wikimedia.org",
    description="Wikimedia Cloud Services automation and orchestration cookbooks",
    extras_require=extras_require,
    install_requires=install_requires,
    keywords=["wmf", "automation", "orchestration", "cookbooks", "wmcs"],
    license="GPLv3+",
    name="wmcs-cookbooks",
    packages=(find_packages(exclude=["*.tests", "*.tests.*"]) + find_namespace_packages(include=["cookbooks.*"])),
    platforms=["GNU/Linux"],
    setup_requires=setup_requires,
    use_scm_version=True,
    url="https://gerrit.wikimedia.org/g/cloud/wmcs-cookbooks",
    zip_safe=False,
)
