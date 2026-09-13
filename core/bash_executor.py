# -*- coding: utf-8 -*-
"""
轻量级 Bash 执行工具 — 专用于执行 agent_reach/ 目录下的 CLI 脚本。

超时 30s，捕获 stdout/stderr，code != 0 时抛出 BashError 触发 Master Agent 降级。
"""

from __future__ import annotations

import subprocess


class BashError(RuntimeError):
    """Bash 执行失败，附带格式化错误信息供 Master Agent 记录与降级。"""


def run_command(cmd: str, timeout: int = 30) -> str:
    """
    执行 shell 命令并返回 stdout 字符串。

    Args:
        cmd: 要执行的完整命令（shell=True）
        timeout: 超时秒数，默认 30s

    Returns:
        标准输出字符串（已 strip）

    Raises:
        BashError: 命令返回非零退出码、超时或无法启动时
    """
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise BashError(f"命令超时（>{timeout}s）：{cmd}")
    except OSError as exc:
        raise BashError(f"命令无法启动：{exc}  cmd={cmd}")

    if result.returncode != 0:
        stderr = result.stderr.strip()[:500]
        raise BashError(
            f"命令退出码 {result.returncode}\n"
            f"stderr: {stderr}\n"
            f"cmd: {cmd}"
        )

    return result.stdout.strip()
