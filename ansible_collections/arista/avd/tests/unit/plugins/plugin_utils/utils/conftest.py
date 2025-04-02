# Copyright (c) 2025 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the LICENSE file.
"""Fixtures for testing the utils module."""
import pytest
import logging


@pytest.fixture
def anta_record() -> logging.LogRecord:
    """Create a log record from an ANTA library."""
    return logging.LogRecord(
        name="anta.runner",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Hello from ANTA",
        args=(),
        exc_info=None
    )


@pytest.fixture
def non_anta_record() -> logging.LogRecord:
    """Create a log record from a non-ANTA library."""
    return logging.LogRecord(
        name="pyavd",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Hello from PyAVD",
        args=(),
        exc_info=None
    )


@pytest.fixture
def warning_record() -> logging.LogRecord:
    """Create a warning log record from an ANTA library."""
    return logging.LogRecord(
        name="anta.runner",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="Warning from ANTA",
        args=(),
        exc_info=None
    )
