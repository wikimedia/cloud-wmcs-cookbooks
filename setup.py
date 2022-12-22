"""Package configuration."""

from setuptools import find_namespace_packages, find_packages, setup

# The below list is only for CI
# For prod add the libs to modules/profile/manifests/spicerack.pp
install_requires = [
    'pyyaml',
    'wikimedia-spicerack',
    'defusedxml',
    'requests[socks]',
]

# Extra dependencies
extras_require = {
    # Test dependencies
    'tests': [
        'pytest>=6.1.0',
        'pre-commit',
    ],
}

setup_requires = [
    'setuptools_scm>=1.15.0',
]

setup(
    author='Riccardo Coccioli',
    author_email='rcoccioli@wikimedia.org',
    description='Wikimedia Cloud Services automation and orchestration cookbooks',
    extras_require=extras_require,
    install_requires=install_requires,
    keywords=['wmf', 'automation', 'orchestration', 'cookbooks', 'wmcs'],
    license='GPLv3+',
    name='wmcs-cookbooks',
    packages=(
        find_packages(exclude=['*.tests', '*.tests.*'])
        + find_namespace_packages(include=["cookbooks.*"])
    ),
    platforms=['GNU/Linux'],
    setup_requires=setup_requires,
    use_scm_version=True,
    url='https://gerrit.wikimedia.org/g/cloud/wmcs-cookbooks',
    zip_safe=False,
)
