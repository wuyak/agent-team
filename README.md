# Agent Team

Agent Team 帮主代理判断是否值得委派、选择角色、交接上下文，并验收、整合子代理的结果。协作配置由用户选择采用。

本仓库提供 Agent Team Skill 及配套配置，可单独安装使用。

## 安装位置

`<codex-home>` 默认 `~/.codex`，设置了 `CODEX_HOME` 时使用实际目录。

| 来源 | 用途与目标位置 |
| --- | --- |
| [skill/](skill/) | 方法、参考与脚本 → `<codex-home>/skills/agent-team/` |
| [roles/](roles/) | 6 个角色 → `<codex-home>/agents/` |
| [agent-team-policy.toml](agent-team-policy.toml) | 版本、模型别名、当前目标服务档位与角色文件名映射 → `<codex-home>/agent-team-policy.toml` |
| [config.fragment.toml](config.fragment.toml) | 合并到 `<codex-home>/config.toml` 的 `[agents]` |

由接收方 Codex 检查原生子代理能力、角色发现方式与可用模型，比较已有内容后安装和合并。已有 `[agents]` 时合并键，保留其他配置。安装后可用 `$agent-team` 调用，也可按需加入自己的 `AGENTS.md`。新会话中确认角色识别，并在真实委派时核对所用模型、权限与结果。

## 角色与模型

当前配置可按接收方可用模型调整：

| 角色 | 职责 | 当前模型／推理强度 |
| --- | --- | --- |
| `default` | 默认承担范围明确的通用与工程任务 | Luna / xhigh |
| `explorer` | 只读检索与证据发现 | Luna / high |
| `reviewer` | 独立只读审查 | Luna / high |
| `worker` | 同类执行任务需要更多推理时使用，可直接选择 | Luna / max |
| `monitor` | 持续观察已运行目标 | Luna / medium |
| `sol_xhigh` | 处理语义冲突、竞争解释等难题 | Sol / xhigh |

角色 TOML 是实际 `model`、`model_reasoning_effort` 和 `sandbox_mode` 的来源；策略只保存模型别名、当前目标服务档位和 `roles.<role>.filename` 映射。完整模型名、压缩阈值和权限设置以 TOML 为准，需当前客户端和账号支持。角色中的 `sandbox_mode` 表达权限意图，实际隔离由父任务与运行时决定。

角色改名或合并时，更新 `roles` 中的文件名映射和对应的 `roles/` 文件，并在新会话中确认角色识别。

需要增设长期承担某类工作的项目代理时，读[专业代理参考](skill/references/professional-agents.md)，判断是否值得独立配置，以及如何划分工具、Skill 与代理的职责。

更换模型或档位时同步核对：

- 角色 TOML 的 `name`、`model`、`model_reasoning_effort`、`sandbox_mode`，以及策略 `roles.<role>.filename` 指向的文件名。
- 策略 `models` 的模型、别名和当前目标服务档位，以及全局默认子代理配置。
- 普通服务档位在策略中为 `standard`，角色文件中为 `default`；Fast 需账号和客户端支持，并核对相关 feature。

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
