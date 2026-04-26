# Chariot 0.2.0 功能清单

> **当前版本**:`0.2.0`(开发中)
> **上一版归档**:[`docs/history/0.1.0/FEATURE.md`](history/0.1.0/FEATURE.md)
>
> 进度标记:✅ 完成 · 🟡 跳过 · ⏸️ 暂缓 · 未打 = 待做。
> 每步完成后按 CLAUDE.md 的 gate 流程:跑验证 → 用户确认 → 打进度标记 → 下一步。

---

## 总览

0.2.0 的目标:把 chariot 从"三协议平等的 echo 骨架"变成"单协议(Anthropic Messages)+ 可配置真实模型"的可用版本。

四块工作:

- **A 单协议化**:删 0.1.0 的三协议平等代码(端点 / Protocol 枚举 / Mock 三协议 / SDK / CLI)
- **B 配置 + Registry**:加 `ChariotConfig` / `ConfigLoader` / `ModelRegistry` / `Agent.install_from_config`
- **C 第一个真模型**:`AnthropicModel`(透传到 Anthropic API)
- **D Model 切换控制面**:`/admin/models` 端点 + CLI `chariot model list / use` + UI Dashboard 切换

每个步骤 = 一个可验收 commit。

---

## A. 单协议化(收窄基础)

### ✅ A.1 删除 OpenAI 两端点

- 删 `POST /v1/chat/completions` 和 `POST /v1/responses` 路由(`chariot/server/controller/dataplane.py`)
- 删对应测试(`tests/server/test_dataplane.py` 的两协议覆盖)
- README 加"OpenAI 客户端怎么接 chariot"小节,推荐 LiteLLM
- **验证**:`uv run pytest -q tests/server/test_dataplane.py` 通过;`curl /v1/chat/completions` 返回 404

### ✅ A.2 收窄 Model / Agent 接口(含 mock 简化)

- `Model.respond` 签名移除 `protocol` 参数
- `Agent.handle` 同步移除 `protocol` 参数,Controller 传一个少一个
- MockModel 收敛到 Messages-only(原 A.3 内容并入,因为接口收窄直接驱动 mock 三协议 dispatch 崩塌)
- 服务器侧 tests 同步(`test_agent` / `test_dataplane` / `test_model_mock`)
- **保留** `chariot/shared/protocols.py`:SDK / CLI 仍在用,延后到 A.4 删除
- **验证**:`uv run pyright chariot/` 通过;`uv run pytest -q` 全绿

### ✅ A.3 简化 MockModel

> 已并入 A.2 —— Model 接口去掉 protocol 参数后,mock 的三协议 dispatch 必然崩塌,
> 一起做更连贯。原文件 ~456 行 → ~190 行。

### ✅ A.4 SDK / CLI 单协议化 + 删 protocols.py

- `chariot/sdk/_adapters.py`:删除或瘦成只剩 messages
- `chariot/sdk/client.py`:`post_chat` / `stream_chat` 移除 protocol 维度
- `chariot/sdk/chat.py` / `chariot/sdk/streams.py`:同步精简
- `chariot/cli/commands/chat.py`、`chariot/cli/core/repl.py`、`chariot/cli/core/context.py`:
  删 `--protocol` 选项 + fmt 参数
- **删除** `chariot/shared/protocols.py`(SDK / CLI 不再 import 后即可)
- `docs/guides/cli-typer.md` 同步更新(若提及 --protocol)
- **验证**:`uv run pytest -q tests/sdk/ tests/cli/` 通过;`chariot chat "hi"` 工作

---

## B. 配置 + Registry 骨架

### ✅ B.1 ChariotConfig + ConfigLoader

- 新增 `chariot/server/config.py`:`ModelEntry` / `ChariotConfig` / `ConfigLoader`
- 用 `tomllib`(Python 3.11+ stdlib)解析
- DEFAULT_PATH = `~/.chariot/config.toml`,env `CHARIOT_CONFIG` 可覆盖
- 单元测试 `tests/server/test_config.py`:合法 / 非法 / 缺失 / env 覆盖
- **验证**:`uv run pytest -q tests/server/test_config.py` 通过

### ✅ B.2 ModelRegistry + Agent.install_from_config

- 新增 `chariot/server/model/registry.py`:`ModelRegistry`(`@register` / `build` / `known_types`)
- `Agent` 加 classmethod `install_from_config(config: ChariotConfig)`
- 暂时只让 `MockModel` 注册成 `type="mock"`(真实模型在 C 阶段)
- 测试 `tests/server/test_model_registry.py`(注册 / 构造 / 未知 type 报错)
- **验证**:`uv run pytest -q tests/server/` 通过

### B.3 lifespan 接 config

- `chariot/server/app.py` 的 lifespan 改成 `Agent.install_from_config(ConfigLoader.load())`
- 无 config 文件时表现 = 0.1.0 现状(走 MockModel)
- 端到端测试覆盖有 / 无 config 两种启动场景
- **验证**:删除 `~/.chariot/config.toml` 后 server 启动 + curl /v1/messages → mock echo

---

## C. AnthropicModel

### C.1 AnthropicModel 非流路径

- 新增 `chariot/server/model/anthropic.py`:类 + `from_config` + 非流 `respond`
- httpx async client 持久化(`__init__` 建一次)
- model_id 改写:client body 里的 `model` 字段替换成配置 `model_id`
- 错误映射:401/403 → 502;429 透传;5xx → 502;超时 → 502
- 用 `respx` 写测试覆盖上述路径
- **验证**:`uv run pytest -q tests/server/test_model_anthropic.py` 通过

### C.2 AnthropicModel 流式路径

- httpx `client.stream()` + `StreamingResponse`,直接转发 SSE 字节
- 流式中途断开:不伪造事件,直接断 TCP
- 流式错误码:首响应已发后只能断;首响应前可正常返 502
- **验证**:respx 流式 fixture 测试通过;手工 `curl --no-buffer -d '{"stream":true,...}'` 看到逐 chunk 输出

### C.3 文档 + 配置示例

- README 加"接 Anthropic 真实 API"小节(配置示例 + ANTHROPIC_API_KEY 设置)
- 可选 `docs/guides/real-models.md`
- **验证**:用户按文档配置 → server 启动 → 真实对话能 round-trip

---

## D. Model 切换控制面

### D.1 /admin/models 端点

- `GET /admin/models` → `{available, active, types}`
- `POST /admin/models {name}` → rebuild Model + `Agent.install` 覆盖 + 返回新 active
- 失败(unknown name / build error)→ 400/500 + 错误信息
- **验证**:`uv run pytest -q tests/server/test_admin.py` 通过

### D.2 CLI `chariot model list / use`

- 新增 `chariot/cli/commands/model.py` 子命令组
- `model list` 调 `/admin/models` 显示 available + active 标记
- `model use <name>` 调 POST + 显示切换结果
- **验证**:本地启动 server,`chariot model list` / `chariot model use claude-opus` 工作

### D.3 UI Dashboard 切换面板

- React Dashboard 加"Active Model"卡片:下拉 + 切换按钮
- 切换成功后刷新 status 显示
- **验证**:Tauri / Vite 启动 → Dashboard 切换可见、生效

---

## 收尾

### Z.1 文档 + 版本号

- `pyproject.toml` 升 `0.1.0 → 0.2.0`
- `pyproject.description` 改写(去掉 0.1.0 留下的"格式转换中枢"陈述,与新定位一致)
- README 顶部说明 0.2.0 状态
- **验证**:`uv run ruff check . && uv run ruff format --check . && uv run pyright chariot/ && uv run pytest -q` 全套通过

---

## 0.3.0+ 路标

详见 `docs/ROADMAP.md`。本表只到 0.2.0 收尾。
