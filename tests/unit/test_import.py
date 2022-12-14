"""Test the import of all the cookbooks."""
import importlib
import pathlib
import os

from pkgutil import iter_modules
from setuptools import find_packages

import pytest


def get_modules():
    """Collect all the cookbook packages and modules."""
    base_package = "cookbooks"
    base_path = pathlib.Path(os.getcwd()) / base_package
    modules = set()
    for package in find_packages(base_path):
        modules.add(f"{base_package}.{package}")
        package_path = base_path / package.replace(".", "/")
        for module_info in iter_modules([str(package_path)]):
            if not module_info.ispkg:
                modules.add(f"{base_package}.{package}.{module_info.name}")

    return modules


@pytest.mark.parametrize('module_name', get_modules())
def test_import(module_name):
    """It should successfully import all defined cookbooks and their packages."""
    importlib.import_module(module_name)  # Will raise on failure
