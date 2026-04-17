"""Exclude the tiny_py_repo fixture from top-level pytest collection.

The fixture has its own test suite (tests/fixtures/tiny_py_repo/tests/) that
is meant to run against a fresh copy of the fixture with its package
installed — NOT against the bug_exam test environment. Pytest would
otherwise try to collect those tests at the top level and fail with
ModuleNotFoundError: tiny_pkg.
"""
collect_ignore_glob = [
    "tests/fixtures/**",
]
