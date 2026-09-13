# -*- coding: utf-8 -*-
"""
Reach-Browser — Streamlit 主入口

双轨策略：
  QUERY_ONLY      → 优先 Agent-Reach Bash/API，失败自动降级浏览器
  ACTION_REQUIRED → 直连 browser-use 浏览器 Agent
"""

from __future__ import annotations

import sys
import os

import streamlit as st

# 确保项目根目录和两个原生项目路径均可被导入
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in [
    _PROJECT_ROOT,
    "D:/Code/browser-use",
    "D:/Code/agent-reach/Agent-Reach",
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.callbacks import StreamlitCallback
import core.master_agent as master_agent

# ── 页面配置 ──────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Reach-Browser",
    page_icon="🌐",
    layout="centered",
)

st.title("🌐 Reach-Browser")
st.caption("双轨协同 · 优先 API/CLI，自动降级浏览器视觉 · 由 Claude Opus 5 驱动")

# ── 输入区 ────────────────────────────────────────────────────────────────────

user_input = st.text_area(
    "请输入你的任务",
    placeholder=(
        "例如：查询 B站 Hot100 并分析前 100 名的主要分区\n"
        "或：帮我在某网站填写表单并提交"
    ),
    height=120,
)

run_btn = st.button("▶ 执行", type="primary", disabled=not user_input.strip())

# ── 执行区 ────────────────────────────────────────────────────────────────────

if run_btn and user_input.strip():
    st.divider()

    with st.status("Agent 运行中...", expanded=True) as status_ui:
        cb = StreamlitCallback(status_ui)

        try:
            result = master_agent.run(user_input.strip(), status_cb=cb)
            status_ui.update(label="✅ 完成", state="complete", expanded=False)
        except Exception as exc:
            status_ui.update(label="❌ 出错", state="error", expanded=True)
            st.error(f"执行异常：{exc}")
            result = None

    if result:
        st.divider()
        st.subheader("📋 结果")
        st.markdown(result)

    # 展开执行日志
    if cb.messages:
        with st.expander("查看完整执行日志", expanded=False):
            for msg in cb.messages:
                st.text(msg)
