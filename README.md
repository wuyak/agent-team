# Agent Team

Agent Team 让 Codex 的主代理把工作交给原生子代理去做。它由两部分组成：

- **Skill**：告诉主代理哪些工作值得委派、选哪个角色和推理档位、任务说明要写什么，以及怎样等待和验收结果。
- **角色文件**：规定每类子代理负责什么、用什么推理档位。

委派是可选的。主代理判断自己做更简单时，就不派子代理。

本仓库可以单独安装；同一份内容也收录在 [zakuro-skills](https://github.com/zakuro-lab/zakuro-skills/tree/main/needs-configuration/agent-team) 中。

## 安装

`<codex-home>` 默认是 `~/.codex`；设置了 `CODEX_HOME` 时，以它为准。

| 仓库里的位置 | 装到哪里 |
| --- | --- |
| [skill/](skill/) | `<codex-home>/skills/agent-team/` |
| [roles/](roles/) 里的 5 个角色文件 | `<codex-home>/agents/` |
| [config.fragment.toml](config.fragment.toml) | 合并进 `<codex-home>/config.toml` 的 `[agents]` |

可以让 Codex 按[配置参考](skill/references/configuration.md)来装。合并 `[agents]` 时保留你的其他配置；如果你在已装的角色文件里固定过模型或档位，更新时保留这些值。

装好后，新开的 Codex 会话才会用上。运行 `codex doctor`，启动警告里没有角色相关的内容，就说明角色文件都加载了。

### 从旧版升级

旧版的策略文件和三个脚本（模型切换、服务档位、配置校验）已经去掉。升级时删除：

- `<codex-home>/agents/sol-xhigh.toml` 和 `worker-max.toml`。Codex 会加载 `agents/` 里所有的 `.toml`，不删的话这两个角色还会出现。
- `<codex-home>/agent-team-policy.toml`。
- `<codex-home>/skills/agent-team/scripts/`。

原来靠脚本做的事，现在这样做：换模型或档位，直接改角色文件；换服务档位，用客户端自带的选择器；检查角色文件，运行 `codex doctor`。

## 角色

| 角色 | 什么时候用 | 推理档位 |
| --- | --- | --- |
| `default` | 调查加小改动、又不适合其他角色的有限任务 | `medium`，难时 `high` |
| `worker` | 要改文件或产出交付物：实现、修复、复现、验证、写作 | `high`，难时 `xhigh` |
| `explorer` | 只读检索：从代码、文档、日志、网页等来源找事实，回答一个具体问题 | `medium`，难时 `high` |
| `reviewer` | 独立的只读审查：按要求检查某个结论、改动、方案或成品 | `high`，难时 `xhigh` |
| `monitor` | 反复观察一个已在运行的命令、进程或任务，直到指定的结束条件 | 固定 `medium` |

每个角色文件的主要内容分两段，读者不同：

- `description` 写给主代理看。派任务工具会列出所有角色的描述，主代理据此选角色。描述的最后一行写这个角色的两档推理档位。
- `developer_instructions` 写给子代理看，只加载进用这个角色的子代理，主代理和其他子代理都看不到。它由角色自己的职责，加上五个角色共用的一段约定组成。共用约定要求子代理：不越出任务范围，不再派子代理，卡在推理上时说明难点，最后交回一份完整、有出处、不超过约 8000 字符的结果。

`explorer`、`reviewer`、`monitor` 在角色文件里固定用 `gpt-5.6-luna`，主代理传什么模型都无效。`default` 和 `worker` 不固定模型，使用 `[agents]` 里的默认子代理模型；只有你为某次委派点名模型时，主代理才会指定。

## 推理档位

角色的推理档位有三种设法：

- **固定一档**：在角色文件里写 `model_reasoning_effort`。Codex 强制使用这一档，主代理传什么都无效。`monitor` 用的是这种。
- **两档**：在 `description` 最后一行写 ``Effort: `high`; `xhigh` when hard.``。主代理派出子代理时，常规工作传第一档；难点在推理上时传第二档，例如要调和相互矛盾的证据、在几种解释或方案中取舍、跨模块追踪行为。其余四个角色用的是这种。
- **不设**：两样都不写时，主代理不传档位，子代理使用 `[agents]` 的 `default_subagent_reasoning_effort`。这一项也没设时，子代理继承主对话当前的档位：主对话用 `xhigh`，子代理也是 `xhigh`。

子代理的档位在派出时定下，之后给它追加任务也不会变，因为 Codex 追加任务和发消息的工具都没有档位参数。子代理交回的结果如果说明它卡在推理上，而不是缺信息，主代理会新派一个第二档的子代理接手，并把前一个子代理的发现写进任务说明。

仓库里的角色都不使用 `max`。

## 修改或新增角色

角色可以增删改，Skill 里没有写死角色名单。修改时注意：

- 必填字段是 `name`、`description`、`developer_instructions`。可选字段有 `model`、`model_reasoning_effort`，以及控制输出的 `model_reasoning_summary`、`model_verbosity`、`personality`。
- 档位行照 ``Effort: `第一档`; `第二档` when hard.`` 的格式，写在描述的最后一行。
- 新角色的指令末尾照抄共用约定，从任意一个现有角色里复制即可。
- 不要加 Codex 不认识的字段：只要有一个未知字段，Codex 就会丢弃整个角色。`nickname_candidates` 只能用英文字母、数字、空格、连字符和下划线。
- 改完运行 `codex doctor`。格式错误、缺少必填字段或重名的角色，会出现在启动警告里。

下面这些设置写在角色文件里不会生效（[openai/codex#39299](https://github.com/openai/codex/pull/39299)，2026 年 8 月起；社区复现见 [openai/codex#40130](https://github.com/openai/codex/issues/40130)）：

| 设置 | 实际由什么决定 |
| --- | --- |
| `sandbox_mode` | 子代理沿用主任务的权限。只读角色靠指令约束，不是运行时隔离 |
| `mcp_servers` | 子代理沿用主会话的 MCP 服务器 |
| `service_tier` | 子代理跟随主任务的服务档位，用客户端的选择器切换 |
| 上下文窗口和自动压缩 | 按模型设置，见下一节 |

字段的完整说明见[配置参考](skill/references/configuration.md)。要为项目增设长期承担某类工作的代理，见[专业代理参考](skill/references/professional-agents.md)。

## 上下文窗口

上下文窗口只能按模型设置：在根级 `model_catalog_json` 指向的自定义模型目录里，改对应模型的 `context_window`。子代理用哪个模型，就用那个模型的窗口。目录里没写 `auto_compact_token_limit` 时，Codex 在窗口的 90% 处自动压缩。

不要在 `config.toml` 根级写 `model_context_window`：它会盖掉所有模型的设置，子代理也会继承。

自定义目录会替换服务器下发的模型列表。客户端或模型更新后，要从最新的 `<codex-home>/models_cache.json` 重建目录，再把窗口设置补回去；否则新模型不会出现在列表里。

## 推荐配置：减少无效等待

主代理等子代理时，如果每次只等很短时间，超时后又要重新调用模型，会白白消耗 token。建议在 `config.toml` 的 `[features]` 里合并：

```toml
[features]
sleep_tool = false
multi_agent_v2 = { enabled = true, min_wait_timeout_ms = 300000, default_wait_timeout_ms = 300000, max_wait_timeout_ms = 3600000 }
```

- `sleep_tool = false` 关掉普通的休眠工具，免得主代理用短休眠反复查看结果。
- `wait_agent` 的最短和默认等待设为 5 分钟，最长 1 小时。子代理完成或发来消息时，主代理仍会被提前唤醒，拿到结果不会因此变慢。

这些字段在 Codex 0.153.4 上验证过，说明见[官方配置 schema](https://learn.chatgpt.com/docs/config-schema.json)。

## 排障

| 现象 | 检查什么 |
| --- | --- |
| 找不到角色 | `codex doctor` 的启动警告；`name` 是否为空或重名；有没有未知字段；`nickname_candidates` 有没有非 ASCII 字符 |
| 档位或模型和预期不同 | 角色文件是否固定了这一项；没固定时，看主代理派出时传了什么，再看 `[agents]` 的默认值 |
| 模型不可用 | 账号权限、模型名，以及这个模型是否支持所选档位 |

## 相关

回顾历史会话里的委派和交付，可以另装 [codex-session-audit](https://github.com/zakuro-lab/zakuro-skills/tree/main/skills/codex-session-audit)，其中的 agent-team-audit 子 Skill 专门审计委派。Agent Team 的日常使用不依赖它。
