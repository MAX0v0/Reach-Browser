# Reach-Browser 项目二次开发需求说明

## 项目基本信息

### 源代码地址
- **browser-use**: `D:\Code\browser-use`
- **agent-reach**: `D:\Code\agent-reach\Agent-Reach`

### 开发环境
- **Python 版本**: 3.12

### 开发前置要求
在二次开发前需要完全通读上面两个项目的所有代码才能进行行动/二次项目开发。

---

## 一、整体架构设计原则

为了在最大程度上保留 browser-use 和 Agent-Reach 两个项目原有代码架构与生态的前提下完成二次开发，我们需要采取"以其中一个核心项目为主主体，通过模块化扩展的方式融合另一个项目，并在最外层叠加总控 Master Agent"的协同设计模式。

### 1. 原生结构优先

不打乱或重构两个项目已有的目录规范。以 browser-use 或独立工作流管理架构为基础目录结构，将 Agent-Reach 的 API / CLI 技能作为独立的 `skills/` 工具集直接挂载，避免侵入改动原项目的底层 core 代码。

### 2. 统一大模型（LLM）基座

系统内所有 Agent（总控 Master Agent、browser-use 内置 Agent、Agent-Reach 的解析器）统一采用 **Claude Opus 5**。

**配置机制**：
- 由于 Opus 5 模型支持自定义 API Key 与代理中转/自定义请求路径（Custom Base URL）
- 系统必须在全局配置文件中提供统一的 `BASE_URL` 和 `API_KEY` 接口
- 供 LangChain 和 Anthropic SDK 实例化时动态加载

### 3. 交互入口（UI/CLI Layer）

采用 **Streamlit** 构建 Web 可视化交互面板作为统一入口。

**功能要求**：
- 接收用户 Prompt
- 实时展示 Master Agent 的思考过程
- 显示当前调用的组件（Bash API 还是浏览器 GUI）
- 展示自动降级的日志信息

---

## 二、系统的逻辑流转与调度（双轨策略）

用户在 Streamlit 输入指令后，最外层的 **Master Agent**（基于 Claude Opus 5）接管请求，按以下规则调度：

### 1. 意图分类与判别

Master Agent 首先根据 Prompt 判别任务类型：

- **数据检索/查询类（QUERY_ONLY）**
  - 例如："查询 B 站 Hot100 并分析前 100 名的主要分区"

- **网页交互/动作操作类（ACTION_REQUIRED）**
  - 例如："帮我在指定网站填写表单、登录并提交"

### 2. QUERY_ONLY（纯查询）的降级流转逻辑

#### 优先链路（Agent-Reach 协议层）

Master Agent 检索 Agent-Reach 的路由定义（如 `skill.md` 或配置路由字典）。

- 若存在该平台路由（例如 bilibili），优先通过 Bash 工具链按顺序执行该平台的内置轻量级工具：
  1. **CLI 工具** (bilicli)
  2. **MCP 服务** (bilimcp)
  3. **API 抓包/直接 HTTP 请求**

#### 降级链路（browser-use 浏览器视觉层）

- 若 Agent-Reach 中无此平台的 API 路由
- 或者 API 执行返回错误/超时/触发了反爬风控

Master Agent 捕获异常后，自动且无缝降级至 browser-use 子系统：
- 启动 Playwright 驱动浏览器访问目标网页
- 提取 DOM 或利用 Vision 视觉能力提取所需数据

#### 数据汇总

最后将原始数据交回 Master Agent，利用 Claude Opus 5 的分析能力整理出最终答案（如得出"游戏区占比最高"的结论）并呈现给用户。

### 3. ACTION_REQUIRED（网页操作）的直连逻辑

识别到任务以"做事/填表/点击/自动化流程"为主时：
- 绕过 Agent-Reach
- 直接启动 browser-use 引擎

browser-use 依据任务要求在浏览器界面中完成：
- 表单填充
- 按钮点击
- 页面跳转等操作

将最终结果或截屏状态反馈给 Streamlit。

---

## 三、基于原生结构的二次开发目录结构

项目在 D 盘根目录（如 `D:\Code\Reach-Browser`）下部署，在融合两个项目原有结构的前提下，模块组织如下：

```
Reach-Browser/
│
├── .env                       # 全局配置文件：保存自定 API Key、Base URL 与代理设置
├── config.py                  # 统一读取环境变量，配置 Claude Opus 5 的 Client 参数
│
├── agent_reach/               # 保留 Agent-Reach 原生项目结构与 API/CLI 技能库
│   ├── skills/                # 各平台 Markdown 技能说明 (如 skill.md)
│   └── platform_scripts/      # 平台 API / CLI 自动化脚本 (如 bilicli 等)
│
├── browser_use/               # 保留 browser-use 原生 SDK 结构
│   ├── agent/                 # 原生 BrowserAgent 逻辑
│   ├── dom/                   # 原生 DOM 剪枝与解析
│   └── controller/            # 动作控制器
│
├── core/                      # 本次二次开发新增的外层总控模块
│   ├── master_agent.py        # 基于 Claude Opus 5 的总控路由与结果分析 Agent
│   ├── bash_executor.py       # 执行 Agent-Reach 脚本的 Bash 工具封装
│   └── callbacks.py           # Streamlit 实时状态回调处理器
│
├── app.py                     # Streamlit 界面入口
└── requirements.txt
```

---

## 四、各模块详细功能规格与实现要求

### 1. 全局模型与 Key 配置（config.py 与 .env）

**需求**：
- 使用 LangChain 的 `ChatAnthropic` 实例化 Claude Opus 5

**参数配置**：
- 必须显式接收 `.env` 中的 `CUSTOM_BASE_URL` 和 `ANTHROPIC_API_KEY`
- 所有子模块（Master Agent 和 browser-use）统一调用此配置
- 保证不泄露密钥，且随时支持自建代理节点

### 2. 核心路由与总控 Agent（core/master_agent.py）

**需求**：
- 定义主 Prompt，要求 Claude Opus 5 分析用户输入的真实意图
- 输出结构化 JSON，包含：
  - 任务类型（`QUERY_ONLY` / `ACTION_REQUIRED`）
  - 目标平台
  - 是否有 Agent-Reach 对应的 Bash 执行命令

**异常处理与降级控制**：
- 设计 Try-Except 逻辑
- 如果调用 `bash_executor` 失败：
  - 在日志中写入降级状态
  - 向 Streamlit 发送提示
  - 将任务包装为 Browser Task 指令转交给 browser-use

### 3. 轻量级 Bash 执行工具（core/bash_executor.py）

**需求**：
- 利用 Python 的 `subprocess` 封装 Bash 工具
- 专用于执行 `agent_reach/` 目录下的 CLI 脚本（如 Python 脚本、Curl 命令等）

**功能**：
- 设置 Timeout（30s）
- 捕获标准输出（stdout）与错误输出（stderr）
- 当 `Code != 0` 时返回格式化的 Error 消息以帮助 Master Agent 触发自动降级

### 4. 浏览器自动化适配器（browser_use/ 调优）

**需求**：
- 保持 browser-use 核心架构不变
- 在实例化 `BrowserAgent` 时，必须强制传入使用自定义 API 地址与密钥初始化的 Claude Opus 5 LLM 实例
- 开启视觉模式（`use_vision=True`）
- 以便在降级攻坚或网页填表时使用 Opus 5 的强大视觉分析能力

### 5. 前端交互与日志渲染（app.py）

**需求**：
- 搭建 Streamlit 主面板

**交互体验**：
- 提供输入框
- 使用 `st.status` 容器展示 Agent 当前在做的事情，例如：
  - "正在分析意图..."
  - "匹配到 bilibili 路由，尝试 Bash 协议抓取..."
  - "协议抓取失败，正在启动浏览器执行视觉攻坚..."
- 将 LangChain 的 Callback 事件实时绑定至该容器
- 确保过程透明可追踪

---

## 总结

本项目的核心设计理念是：
1. **保持原生架构**：不破坏 browser-use 和 Agent-Reach 的原有结构
2. **双轨协同**：优先使用轻量级 API/CLI，失败时自动降级到浏览器视觉方案
3. **统一 LLM 基座**：所有 Agent 统一使用 Claude Opus 5
4. **可视化交互**：通过 Streamlit 实现透明的执行过程展示
