# Reach-Browser

双轨协同 Agent 系统 — 融合 [browser-use](https://github.com/browser-use/browser-use) 视觉攻坚能力与 [Agent-Reach](https://github.com/yym68686/Agent-Reach) 的零依赖数据检索路由，外层叠加 Claude Opus 5 总控 Master Agent，实现 QUERY_ONLY 检索优先、ACTION_REQUIRED 浏览器降级的智能任务调度。

## 架构

```
用户输入
  ↓
Master Agent（Claude Opus 5 意图分类）
  ├─ QUERY_ONLY → Agent-Reach 三级降级链路
  │   ├─ Tier 1: CLI（bili-cli / gh / yt-dlp）
  │   ├─ Tier 2: MCP（OpenCLI 浏览器会话后端）
  │   └─ Tier 3: API（零依赖 HTTP 直连兜底）
  │
  └─ ACTION_REQUIRED → browser-use 视觉操作
      （登录/填表/点击等需浏览器交互的任务）
```

### 核心模块

- **`config.py`** — 统一 LLM 基座，API Key / Base URL 从 `.env` 动态加载
- **`core/master_agent.py`** — 意图分类与降级调度主控
- **`core/routes.py`** — 三级路由表（CLI → MCP → API），命令语法对齐 Agent-Reach `references/*.md`
- **`core/backend_resolver.py`** — 复用 Agent-Reach channel 体检决定链路顺序（对应 `summarize.py` 的 `active_backend` 逻辑）
- **`core/api_fetch.py`** — 跨平台 API 抓取器（`CookieJar` 维持会话、分页、业务错误码判定）
- **`core/bash_executor.py`** — subprocess 封装，30s 超时
- **`core/callbacks.py`** — Streamlit 实时状态回调
- **`app.py`** — Streamlit 交互面板

## 快速开始

### 1. 环境准备

```bash
# 克隆本项目
git clone git@github.com:MAX0v0/Reach-Browser.git
cd Reach-Browser

# 克隆两个原生项目（以可编辑包形式引入）
git clone https://github.com/browser-use/browser-use D:/Code/browser-use
git clone https://github.com/yym68686/Agent-Reach D:/Code/agent-reach/Agent-Reach

# 安装依赖
pip install -r requirements.txt
pip install -e D:/Code/browser-use
pip install -e D:/Code/agent-reach/Agent-Reach
playwright install chromium
```

### 2. 配置

复制 `.env.example` 为 `.env`，填入你的 Anthropic API Key：

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
ANTHROPIC_API_KEY=sk-ant-your-key-here
CUSTOM_BASE_URL=  # 可选：代理中转节点
```

### 3. 运行

```bash
streamlit run app.py
```

打开浏览器访问 `http://localhost:8501`，输入任务：

- **检索类**："帮我去 bilibili 查询 hot100，并且帮我分析各分区占比"
- **操作类**："帮我登录 GitHub 并提交一个 issue"

## 实现要点

### Agent-Reach 三级降级（QUERY_ONLY）

1. **后端探测优先** — 先调用 `channel.check()` 获取 `active_backend`（对应 `summarize.py` 的做法），把活跃那级提到最前，避免盲试
2. **命令语法严格对齐** — 例如 `bili-cli` 无 `--json` flag，必须用 `bili rank -n 100` 而非编造语法
3. **逐级试错** — CLI 缺失 → MCP 缺失 → API 兜底，任一层拿到实质数据即停
4. **数据有效性校验** — 退出码 0 不代表成功：
   - B站 `code=-352/-412` 是风控限流，需识别后降级
   - 空 `data: []` 判为无效，继续下一级

### API 层（Tier 3）

- **跨平台兼容** — 不拼裸 curl（Windows 下 `bash_executor` 走 cmd.exe，`/tmp`、`/dev/null` 全部无效），改用 Python `urllib`
- **Cookie 会话维持** — 用 `CookieJar` + `HTTPCookieProcessor`；手工解析 `Set-Cookie` 头再拼字符串会丢 cookie，实测导致 B站返回 `-352`
- **B站榜单策略** — 优先 `ranking/v2`（单次 100 条带分区字段），受 IP 限流时自动退 `popular` 翻页兜底
- **分页与去重** — 搜索类端点按 `bvid` 去重，翻页至拿满 `n` 条或遇空页

### browser-use 配置（ACTION_REQUIRED）

- **摘除 `evaluate` 动作** — 模型常把参数名写成 `javascript` / `js_code`，而 schema 要求 `code`，会引发 46 条 union 校验错误并在 5 次失败后终止 Agent
- **`fallback_llm=llm`** — 格式错误时用同模型重试而非直接终止
- **`max_failures=10`** — 容错余量从默认 5 提到 10
- **`extend_system_message`** — 明确要求：
  - 数据采集用 `extract` 而非 JS 求值
  - 榜单页反复 `scroll` 到底，确保拿满条目数
  - 只有数据确实完整时才 `done(success=true)`

## 测试

```bash
pytest tests/ -v
```

- **105 项测试全部通过**
- LLM 与 HTTP 均 mock，离线可跑
- 覆盖意图分类、路由匹配、降级链路、Cookie 会话、分页去重、风控识别

## 项目结构

```
Reach-Browser/
├── .env.example          # 配置模板
├── .gitignore
├── app.py                # Streamlit 入口
├── config.py             # LLM 统一配置
├── core/
│   ├── master_agent.py   # 意图分类与总控
│   ├── routes.py         # 三级路由表
│   ├── backend_resolver.py  # Agent-Reach 后端探测
│   ├── api_fetch.py      # 跨平台 API 抓取器
│   ├── bash_executor.py  # subprocess 封装
│   └── callbacks.py      # Streamlit 回调
└── tests/                # 105 项测试用例
```

## 依赖

- **browser-use** — Playwright 视觉 Agent
- **Agent-Reach** — 多平台检索路由框架
- **langchain-anthropic** — Claude Opus 5 接入
- **streamlit** — Web 交互面板
- **pytest** — 测试框架

两个原生项目以可编辑包（`pip install -e`）形式引入，核心结构未做改动，升级时直接 `git pull` 即可。

## 已知限制

- **CLI / MCP 层需手动安装** — `bili-cli`、`gh`、`opencli` 等工具未安装时前两级必然失败，但 API 兜底仍可用
- **B站 ranking/v2 间歇限流** — 同一份代码先返回 100 条（`code=0`），连续请求若干次后转 `-352`；已实现 popular 翻页兜底
- **v2ex 需代理** — 部分地区网络直连超时

## License

MIT

---

**技术栈**: Claude Opus 5 · Playwright · LangChain · Streamlit · pytest
