# -*- coding: utf-8 -*-
"""
Streamlit 实时状态回调处理器。

提供 StreamlitCallback 类，将 Master Agent 的状态消息实时写入
Streamlit 的 st.status 容器，同时输出到 Python logging。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # 仅类型提示，不在模块级别导入 streamlit（允许在非 UI 上下文中 import）

logger = logging.getLogger(__name__)


class StreamlitCallback:
    """
    将状态消息转发到 Streamlit st.status 容器。

    用法：
        with st.status("Agent 运行中...", expanded=True) as status_container:
            cb = StreamlitCallback(status_container)
            result = master_agent.run(user_input, status_cb=cb)
    """

    def __init__(self, status_container):
        """
        Args:
            status_container: st.status() 返回的容器对象，
                              或任何实现 .write(str) 的对象。
        """
        self._container = status_container
        self._messages: list[str] = []

    def __call__(self, message: str) -> None:
        """接收一条状态消息，写入容器并记录日志。"""
        logger.info(message)
        self._messages.append(message)
        try:
            self._container.write(message)
        except Exception:
            pass  # 非 Streamlit 上下文时静默忽略

    @property
    def messages(self) -> list[str]:
        """返回本次 Agent 运行累积的所有状态消息。"""
        return list(self._messages)
