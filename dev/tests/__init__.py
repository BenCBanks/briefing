"""Fail closed before importing any test package modules without isolation."""

from dev.test_guard import enforce_test_environment as _enforce_test_environment

_enforce_test_environment()
