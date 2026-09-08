# Agent Team

Agent Team 帮主代理判断是否值得委派、选择角色、交接上下文并验收结果。协作配置由用户选择采用，记录 Hooks 可单独启用。主代理验收并整合子代理的结果。

本仓库提供 Agent Team Skill 及配套配置，可单独安装使用。

## 安装位置

`<codex-home>` 默认 `~/.codex`，设置了 `CODEX_HOME` 时使用实际目录。

| 来源 | 用途与目标位置 |
| --- | --- |
| [skill/](skill/) | 方法、参考与脚本 → `<codex-home>/skills/agent-team/` |
| [roles/](roles/) | 7 个角色 → `<codex-home>/agents/` |
| [agent-team-policy.toml](agent-team-policy.toml) | 模型、档位与变更历史 → `<codex-home>/agent-team-policy.toml` |
| [config.fragment.toml](config.fragment.toml) | 合并到 `<codex-home>/config.toml` 的 `[agents]` |
| [hooks.example.json](hooks.example.json) | 按客户端要求合并到 Hooks 配置 |

由接收方 Codex 检查原生子代理能力、角色发现方式与可用模型，比较已有内容后安装和合并。已有 `[agents]` 时合并键，保留其他配置。安装后可用 `$agent-team` 调用，也可按需加入自己的 `AGENTS.md`。新会话中确认角色识别，并在真实委派时核对所用模型、权限与结果。

## 角色与模型

当前配置可按接收方可用模型调整：

| 角色 | 职责 | 当前模型／推理强度 |
| --- | --- | --- |
| `default` | 有界的通用工作 | Luna / high |
| `explorer` | 只读检索与证据发现 | Luna / medium |
| `reviewer` | 独立只读审查 | Luna / high |
| `worker` | 明确范围的实现、修复与验证 | Luna / xhigh |
| `monitor` | 持续观察已运行目标 | Luna / medium |
| `worker_max` | 普通执行推理不足时升级 | Luna / max |
| `worker_xhigh` | 处理语义冲突、竞争解释等难题 | Sol / xhigh |

完整模型名、压缩阈值和权限设置以 TOML 为准，需当前客户端和账号支持。角色中的 `sandbox_mode` 表达权限意图，实际隔离由父任务与运行时决定。

需要增设长期承担某类工作的项目代理时，读[专业代理参考](skill/references/professional-agents.md)，判断是否值得独立配置，以及如何划分工具、Skill 与代理的职责。

更换模型或档位时同步核对：

- 角色 TOML 的 `name`、`model`、`model_reasoning_effort` 与策略 `roles.<role>`，文件名与 `filename`。
- 策略 `models` 的模型与别名、全局默认子代理配置，以及历史末项与当前值。
- 普通服务档位在策略中为 `standard`，角色文件中为 `default`；Fast 需账号和客户端支持，并核对相关 feature。

## 首次安装：初始化配置历史

仓库中的 `agent-team-policy.toml` 是分发模板，两项历史为空，不附带作者的使用记录。接收方 Codex 确定模型与角色配置后，在本机安装的策略文件中生成初始记录，再运行配套脚本：

- 每个模型生成一条 `[[service_tier_history]]`，包含 `effective_at`、`model`、`service_tier`。
- 每个角色生成一条 `[[role_runtime_history]]`，包含 `effective_at`、`role`、`model`、`reasoning_effort`。

`effective_at` 使用本次配置生效的实际时间（带时区的 ISO 8601 格式），其余值取自最终配置。生成记录时移除文件顶部对应的空数组声明。已有本机历史时保留原记录，实际变更追加新记录。

这两项是配置变更历史，即使不启用记录 Hooks，策略脚本也需要它们；尚未初始化的模板无法通过策略校验。

## 自选：记录 Hooks

脚本要求 Python 3.11+，记录器使用 `fcntl`，面向 macOS/Linux；Windows 原生环境由接收方 Codex 根据本机环境适配路径、Shell 和文件锁，并验证实际运行结果。

示例包含 `PreToolUse`、`PostToolUse`、`SubagentStart`、`SubagentStop`、`Stop`，记录委派与生命周期。采用时：

1. 核对客户端支持的事件、格式、加载位置和信任方式。
2. 合并示例条目，保留其他 Hooks，避免重复安装记录器。
3. 确认 Hook 进程可调用 `python3`，并能正确解析 Codex home；自定义 `CODEX_HOME` 须在该进程生效。示例使用 POSIX Shell，路径引用需保留。
4. 在实际委派后检查 `<codex-home>/agent-team-records/`。事件或字段缺失时按缺失处理。

记录器会读取相关会话片段，输出含会话标识、工作目录等信息，保留在本机。维护采集器或补录时读 [记录说明](skill/references/field-recording.md)。

## 工具与排障

| 脚本 | 用途 |
| --- | --- |
| `agent_policy.py` | 读写策略与历史 |
| `agent_speed.py` | 查看、调整服务档位及检查一致性 |
| `read_thread_once.py` | 经 `codex app-server --stdio` 读取选定任务 |
| `record_hook.py` | 采集 Hook 事件 |
| `record_closeout.py` | 整理、修正和复核记录 |
| `record_common.py` | 两个记录入口共用的解析与分类函数；随脚本一起安装，不单独运行 |

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/agent-team/scripts/agent_speed.py" validate
```

该命令检查策略、历史、角色服务档位和 Fast 设置。模型／推理字段需按上节核对，账号权限、运行时隔离与 Hooks 需实际运行验证。

| 现象 | 检查方向 |
| --- | --- |
| 找不到角色 | Codex home、角色发现规则、文件格式与原生子代理能力 |
| 模型不可用 | 账号权限、模型名与推理强度；同步角色和策略 |
| 历史不一致 | 最新记录与当前模型／档位 |
| Hook 没有记录 | 信任、事件触发、Python、路径与实际记录 |

更新时比较仓库与本机修改；客户端升级后核对配置字段和事件。停用记录只移除对应 Hook 条目，保留已有记录和其他 Hooks。
