# -*- coding: utf-8 -*-
"""
Tests for core/bash_executor.py.

Run with:  pytest tests/test_bash_executor.py -v
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bash_executor import run_command, BashError


# ── happy path ────────────────────────────────────────────────────────────────

def test_run_simple_echo():
    result = run_command("echo hello")
    assert result == "hello"


def test_run_strips_whitespace():
    result = run_command("echo   spaced   ")
    # echo itself trims nothing, but run_command strips stdout
    assert result.strip() == result


def test_run_returns_stdout():
    result = run_command('python -c "print(42)"')
    assert result == "42"


# ── error cases ───────────────────────────────────────────────────────────────

def test_nonzero_exit_raises_bash_error():
    with pytest.raises(BashError, match="退出码"):
        run_command("exit 1", timeout=5)


def test_nonzero_exit_includes_stderr():
    with pytest.raises(BashError) as exc_info:
        run_command('python -c "import sys; sys.stderr.write(\'oops\'); sys.exit(2)"', timeout=5)
    assert "oops" in str(exc_info.value)


def test_timeout_raises_bash_error():
    with pytest.raises(BashError, match="超时"):
        run_command("python -c \"import time; time.sleep(10)\"", timeout=1)


def test_invalid_command_raises_bash_error():
    # A command that exits nonzero (nonexistent binary)
    with pytest.raises(BashError):
        run_command("this_binary_does_not_exist_xyz_123", timeout=5)


# ── BashError is a RuntimeError ───────────────────────────────────────────────

def test_bash_error_is_runtime_error():
    with pytest.raises(RuntimeError):
        run_command("exit 1", timeout=5)
