#!/bin/bash -e
black \
    cookbooks/wmcs \
    wmcs_libs \
    tests

isort \
    cookbooks/wmcs \
    wmcs_libs \
    tests
