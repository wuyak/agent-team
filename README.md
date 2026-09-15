# Agent Team

Agent Team 帮主代理安排分工与依赖、选择角色、交接上下文，围绕事件协调并验收、整合子代理的结果。面向用户的进度更新说明整体变化、剩余工作和证据范围。协作配置由用户选择采用。

本仓库提供 Agent Team Skill 及配套配置，可单独安装使用。

## 安装位置

`<codex-home>` 默认 `~/.codex`，设置了 `CODEX_HOME` 时使用实际目录。

| 来源 | 用途与目标位置 |
| --- | --- |
| [skill/](skill/) | 方法、参考与脚本 → `<codex-home>/skills/agent-team/` |
| [roles/](roles/) | 7 个角色 → `<codex-home>/agents/` |
| [agent-team-policy.toml](agent-team-policy.toml) | 版本、模型别名、当前目标服务档位与角色文件名映射 → `<codex-home>/agent-team-policy.toml` |
| [config.fragment.toml](config.fragment.toml) | 合并到 `<codex-home>/config.toml` 的 `[agents]` |

由接收方 Codex 检查原生子代理能力、角色发现方式与可用模型，比较已有内容后安装和合并。已有 `[agents]` 时合并键，保留其他配置。安装后可用 `$agent-team` 调用，也可按需加入自己的 `AGENTS.md`。新会话中确认角色识别，并在真实委派时核对所用模型、权限与结果。

## 角色与模型

当前配置可按接收方可用模型调整：

| 角色 | 职责 | 当前模型／推理强度 |
| --- | --- | --- |
| `default` | 默认承担范围明确的通用与工程任务 | Luna / high |
| `explorer` | 只读检索与证据发现 | Luna / high |
| `reviewer` | 独立只读审查 | Luna / high |
| `worker` | 同类执行任务需要更多推理时使用，可直接选择 | Luna / xhigh |
| `worker_max` | 与 worker 相同；仅用户明确指定本次委派使用 worker_max 或 Luna/max 时选择 | Luna / max |
| `monitor` | 持续观察已运行目标 | Luna / medium |
| `sol_xhigh` | 处理语义冲突、竞争解释等难题 | Sol / xhigh |

角色 TOML 是实际 `model`、`model_reasoning_effort` 和 `sandbox_mode` 的来源；策略只保存模型别名、当前目标服务档位和 `roles.<role>.filename` 映射。完整模型名、角色压缩阈值和权限设置以 TOML 为准；Luna 的扩展上下文还需要下节的根级模型目录配置，需当前客户端和账号支持。角色中的 `sandbox_mode` 表达权限意图，实际隔离由父任务与运行时决定。

角色改名或合并时，更新 `roles` 中的文件名映射和对应的 `roles/` 文件，并在新会话中确认角色识别。

需要增设长期承担某类工作的项目代理时，读[专业代理参考](skill/references/professional-agents.md)，判断是否值得独立配置，以及如何划分工具、Skill 与代理的职责。

更换模型或档位时同步核对：

- 角色 TOML 的 `name`、`model`、`model_reasoning_effort`、`sandbox_mode`，以及策略 `roles.<role>.filename` 指向的文件名。
- 策略 `models` 的模型、别名和当前目标服务档位，以及全局默认子代理配置。
- 普通服务档位在策略中为 `standard`，角色文件中为 `default`；Fast 需账号和客户端支持，并核对相关 feature。

## Luna 的上下文与自动压缩

本次配置只提高 **`gpt-5.6-luna`** 的上下文和自动压缩阈值，适用于六个 Luna 角色：`default`、`explorer`、`monitor`、`reviewer`、`worker`、`worker_max`。模型目录按模型生效，同一安装中使用 Luna 的主任务也会采用该设置。

| 配置项 | Luna | 其他模型 |
| --- | --- | --- |
| 模型目录的 `context_window` | `872000` | 保留原值；本机为 `272000` |
| 模型目录的 `auto_compact_token_limit` | `700000` | 保留原压缩配置；本机为 `250000` |
| 角色 `model_auto_compact_token_limit` | 六个 Luna 角色均为 `700000` | Sol 角色保持 `250000` |
| `model_auto_compact_token_limit_scope` | `total` | 保持原设置；本机同为 `total` |

本机目录的 `effective_context_window_percent` 为 `95`，因此 Luna 折算的可用上下文为 **828,400 tokens**；`872000` 是配置窗口，`700000` 是自动压缩阈值，两者含义不同。

### 配置模型目录与角色

1. 从接收机器当前的 `<codex-home>/models_cache.json` 复制完整 `models` 数组，保存为 `<codex-home>/model-catalogs/luna-context.json`，顶层结构为 `{"models": [...]}`。已有自定义目录时先合并既有修改。`model_catalog_json` 加载的是完整目录，不能只保存 Luna 一条，也不能省略模型条目的其他字段；本仓库不分发作者机器的模型目录快照。
2. 在 `slug = "gpt-5.6-luna"` 的条目中设置 `context_window = 872000`、`auto_compact_token_limit = 700000`，其余字段保留。先确认接收方目录支持该窗口；本机 Luna 的 `max_context_window` 为 `872000`。其他模型保留原来的窗口与压缩行为。
3. 在 `<codex-home>/config.toml` 的**根级**合并以下设置，放在 `[agents]` 等表头之前，并替换为接收机器的绝对路径：

   ```toml
   model_catalog_json = "/absolute/path/to/codex-home/model-catalogs/luna-context.json"
   model_auto_compact_token_limit_scope = "total"
   ```

   根级 `model_auto_compact_token_limit` 会覆盖模型目录中的值。本次先将原来的 `250000` 保留到其他模型条目的 `auto_compact_token_limit`，再移除根级阈值。接收机器应保留自己的原值，不能把所有模型一并改成 `700000`。同样，不要用根级 `model_context_window = 872000` 来实现仅调整 Luna。
4. 安装本仓库的角色文件。六个 Luna 角色已包含：

   ```toml
   model_auto_compact_token_limit = 700000
   model_auto_compact_token_limit_scope = "total"
   ```

   **角色阈值和模型目录需要配套设置。** 本机桌面客户端 `0.154.0-alpha.6.2` 中，仅在角色 TOML 写 `model_context_window` 不能实现单角色扩窗，因此采用按模型配置目录的方式。只提高压缩阈值，不代表上下文窗口已经扩大。

### 生效与维护

保存配置后重新打开实际使用的客户端，并在正常的新任务中确认 Luna 的窗口与压缩设置。文件解析与数值核对属于静态验证，不代表运行中的会话已经切换，也不需要为此启动临时客户端。

自定义模型目录是完整快照。客户端或可用模型更新后，应以接收机器的最新目录核对新增模型和元数据，再保留上述 Luna 调整及其他模型的既有设置。不要长期沿用旧快照而遗漏后续模型更新。

## 推荐：减少无效等待的 token 消耗

主代理反复短暂等待、超时后重新调用模型，会产生无用的 token 消耗。建议关闭 `clock.sleep`，将 `wait_agent` 的最短和默认等待时间设为 5 分钟。**子代理完成或有新消息时仍可提前唤醒，不会因此推迟结果返回。**

在实际运行 Codex 的机器上，编辑 `~/.codex/config.toml`，将以下设置合并到已有的 `[features]` 中：

```toml
[features]
sleep_tool = false
multi_agent_v2 = { enabled = true, min_wait_timeout_ms = 300000, default_wait_timeout_ms = 300000, max_wait_timeout_ms = 3600000 }
```

- `sleep_tool = false`：关闭普通休眠工具，减少用短休眠反复等结果。
- 最短和默认等待 **5 分钟**：减少没有新消息时的超时唤醒。
- 最大等待 **1 小时**：允许显式请求更长等待，默认仍为 5 分钟。

GPT-5.6（非 Luna）、GPT-6 Astra 等主代理使用 v2 版 `wait_agent` 时，适用以上超时设置，与子代理使用什么模型无关。配置已在 Codex 0.153.4 验证，字段见[官方配置 schema](https://learn.chatgpt.com/docs/config-schema.json)。

## 会话审计

历史执行与子代理复盘由 [codex-session-audit](https://github.com/zakuro-lab/zakuro-skills/tree/main/skills/codex-session-audit) 提供，可另行安装。主 Skill 提供原生会话与父子关系查询，`skills/agent-team-audit/` 子 Skill 负责审计委派与交付，以及比较模型和推理强度。Agent Team 的日常协作不依赖审计 Skill。

## 工具与排障

| 脚本 | 用途 |
| --- | --- |
| `agent_policy.py` | 读写策略 |
| `agent_speed.py` | 查看、调整服务档位 |

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/agent-team/scripts/agent_speed.py" status
python3 "${CODEX_HOME:-$HOME/.codex}/skills/agent-team/scripts/validate_agent_team.py"
```

`agent_speed.py status` 显示模型服务档位及角色档位是否匹配；`validate_agent_team.py` 检查角色文件、当前策略和服务档位。模型／推理字段需按上节核对，账号权限和运行时隔离需实际运行验证。

| 现象 | 检查方向 |
| --- | --- |
| 找不到角色 | Codex home、角色发现规则、文件格式与原生子代理能力 |
| 模型不可用 | 账号权限、模型名与推理强度；同步角色和策略 |
| 角色配置不一致 | 角色 TOML、策略中的文件名映射，以及模型对应的目标档位 |

更新时比较仓库与本机修改，保留用户的其他配置。
