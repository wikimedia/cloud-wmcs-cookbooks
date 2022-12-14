#!/bin/bash -e
black \
    cookbooks/wmcs \
    wmcs_libs \
    tests/unit/wmcs

isort \
    cookbooks/wmcs \
    wmcs_libs \
    tests/unit/wmcs
