# Golden tasks

每个 YAML 一个 task,`chariot/eval/loader.py:GoldenTaskLoader` 加载。

## YAML schema

```yaml
task_id: file_read_pyproject           # 必填;唯一;给 list / filter / diff 用
prompt: "看一下 pyproject.toml 的内容"  # 必填;发给 agent 的 user message
verifier_type: tool_called             # 必填;exact_match / tool_called / file_state / output_schema
expected:                              # 必填;verifier-specific payload
  tool: read_file
  args_subset:
    path: pyproject.toml
category: file                         # 可选;list 默认按 category 分组(默认 "uncategorized")
description: ""                        # 可选;一行 human readable
max_iterations: 10                     # 可选;tool loop 最大轮数(默认 10)
model: null                            # 可选;null = 用 agent 默认 provider
system: null                           # 可选;null = 用 agent_profile / active bundle
```

## verifier_type → expected 字段

| Verifier         | expected 字段                                                    | 检查 |
|---|---|---|
| `exact_match`    | `contains: [str]`(AND)+ 可选 `case_insensitive: bool`           | record.final_response 必须全部包含 |
| `tool_called`    | `tool: str` + 可选 `args_subset: {k: v}`(strict equal)          | tool_calls 至少一项 name+args 匹配 |
| `file_state`     | `path: str` + `exists: bool` + 可选 `contains: str`              | 跑完后文件状态符合 |
| `output_schema`  | `schema: {...}`(JSON Schema)+ 可选 `decode: str` 解析方式      | final_response 解析后符合 schema |

verifier 真实现在 wave 3 加;wave 1 / 2 阶段 schema 校验跑得过即可。

## 添加新 task

1. 在本目录加 `<task_id>.yaml`,task_id 必须仓库内唯一
2. `uv run chariot eval list-tasks` 检查 loader 解析成功
3. wave 3 起 `uv run chariot eval --task <task_id>` 单跑该 task verify

## 子目录组织

允许子目录(loader `rglob`):

```
tests/golden/
├── README.md
├── smoke/
│   └── smoke_oneshot.yaml
├── file/
│   ├── file_read_pyproject.yaml
│   └── ...
└── plan/
    └── ...
```

task_id 跨子目录仍要唯一(loader fail-fast)。
