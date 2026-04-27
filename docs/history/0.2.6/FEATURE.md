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

### ✅ B.3 lifespan 接 config

- `chariot/server/app.py` 的 lifespan 改成 `Agent.install_from_config(ConfigLoader.load())`
- 无 config 文件时表现 = 0.1.0 现状(走 MockModel)
- 端到端测试覆盖有 / 无 config 两种启动场景
- **验证**:删除 `~/.chariot/config.toml` 后 server 启动 + curl /v1/messages → mock echo

---

## C. AnthropicModel

### ✅ C.1 AnthropicModel 非流路径

- 新增 `chariot/server/model/anthropic.py`:类 + `from_config` + 非流 `respond`
- httpx async client 持久化(`__init__` 建一次)
- model_id 改写:client body 里的 `model` 字段替换成配置 `model_id`
- 错误映射:401/403 → 502;429 透传;5xx → 502;超时 → 502
- 用 `respx` 写测试覆盖上述路径
- **验证**:`uv run pytest -q tests/server/test_model_anthropic.py` 通过

### ✅ C.2 AnthropicModel 流式路径

- httpx `client.stream()` + `StreamingResponse`,直接转发 SSE 字节
- 流式中途断开:不伪造事件,直接断 TCP
- 流式错误码:首响应已发后只能断;首响应前可正常返 502
- **验证**:respx 流式 fixture 测试通过;手工 `curl --no-buffer -d '{"stream":true,...}'` 看到逐 chunk 输出

### ✅ C.3 文档 + 配置示例

- README 加"接 Anthropic 真实 API"小节(配置示例 + ANTHROPIC_API_KEY 设置)
- 可选 `docs/guides/real-models.md`
- **验证**:用户按文档配置 → server 启动 → 真实对话能 round-trip

---

## D. Model 切换控制面

### ✅ D.1 /admin/models 端点

- `GET /admin/models` → `{available, active, types}`
- `POST /admin/models {name}` → rebuild Model + `Agent.install` 覆盖 + 返回新 active
- 失败(unknown name / build error)→ 400/500 + 错误信息
- **验证**:`uv run pytest -q tests/server/test_admin.py` 通过

### ✅ D.2 CLI `chariot model list / use`

- 新增 `chariot/cli/commands/model.py` 子命令组
- `model list` 调 `/admin/models` 显示 available + active 标记
- `model use <name>` 调 POST + 显示切换结果
- **验证**:本地启动 server,`chariot model list` / `chariot model use claude-opus` 工作

### ✅ D.3 UI Dashboard 切换面板

- React Dashboard 加"Active Model"卡片:下拉 + 切换按钮
- 切换成功后刷新 status 显示
- **验证**:Tauri / Vite 启动 → Dashboard 切换可见、生效

---

## 收尾

### ✅ Z.1 文档 + 版本号

- `pyproject.toml` 升 `0.1.0 → 0.2.0`
- `pyproject.description` 改写(去掉 0.1.0 留下的"格式转换中枢"陈述,与新定位一致)
- README 顶部说明 0.2.0 状态
- **验证**:`uv run ruff check . && uv run ruff format --check . && uv run pyright chariot/ && uv run pytest -q` 全套通过

---

## 0.2.1 patch

小步增量,不动架构。

### ✅ P.1 `chariot config init / show` + 内置模板

- 新增 `chariot/config.example.toml`(注释完整的模板,含 mock + anthropic 两个 entry 示例)
- 新增 `chariot/cli/commands/config.py`:
  - `chariot config init [--force]`:把内置模板复制到 `~/.chariot/config.toml`;
    默认拒绝覆盖,`--force` 强行
  - `chariot config show`:打默认路径 + env `CHARIOT_CONFIG`(若设)+ 当前内容,
    排错用
- 模板用 `importlib.resources.files("chariot")` 读;hatch wheel 自动收录 `.toml`
- 测试 `tests/cli/test_config.py` 6 个用例:
  - init 在缺失 / 已存在 / `--force` 三种情况下的行为
  - show 报告默认 / 文件缺失 / env 覆盖
- README "接 Anthropic" 步骤 2 改成 `chariot config init`,手写 toml 作为备选
- pyproject + `__init__` 升 `0.2.0 → 0.2.1`
- **验证**:`uv run ruff check . && uv run ruff format --check . && uv run pyright chariot/ && uv run pytest -q` 全套通过

---

---

## 0.2.2 patch

### ✅ P.2 Chat 页单协议化清理

- 0.2.0 已经把 server 端 / SDK / CLI 单协议化(只接 `/v1/messages`),但 React 端
  `Chat.tsx` 还残留 0.1.0 的"协议下拉 + 模型下拉 + 自定义 model 输入"控件 ——
  这些选择项在 server 侧已被 active config 改写,UI 上选什么都不再影响实际行为,
  纯混淆用户。本步把这些遗留 UI 全部摘掉:
  - `packages/app/src/lib/api.ts`:删 `Protocol` 枚举 / `DEFAULT_MODELS` /
    `MODEL_CHOICES` 等三协议时代的常量
  - `packages/app/src/lib/streams.ts`:`ChatStream` 移除 `Protocol` 入参,
    单一处理 Messages SSE
  - `packages/app/src/lib/chat.ts`:`runTurn` 删 `fmt` 字段,固定 POST
    `/v1/messages`;`ChatTurnOpts.model` 仅作为 body.model 传给 server(供
    `logs.model` 显示)
  - `packages/app/src/pages/Chat.tsx`:删除协议 / 模型下拉 + 自定义输入,
    改成 mount 时拉 `/admin/models.active`(fallback 到 `/admin/status.model`)
    展示当前 active model;旁边给 "在 Dashboard 切换" 链接,引导到 D.3 的
    切换面板做实际切换
- pyproject + `chariot/__init__` 升 `0.2.1 → 0.2.2`
- **验证**:`bun run --filter=@chariot/app build`(tsc + vite build)通过;
  Python 全套(`uv run ruff check . && uv run ruff format --check . &&
  uv run pyright chariot/ && uv run pytest -q`)保持全绿(本步纯前端,
  不影响 server 测试)

---

---

## 0.2.3 patch

### ✅ P.3 Model 切换 UI 从 Dashboard 挪到 Chat 页

- P.2 仍把 model 切换面板留在 Dashboard,Chat 页只读展示 + "在 Dashboard 切换"
  跳转。但 model 选择本质上是"发消息前的上下文设置",每次跳页才能切既反直觉
  又增加摩擦。本步把切换 UI 移到 Chat 页顶部、原地切换;Dashboard 保留只读卡片
  作为 server 状态总览的一部分。
- `packages/app/src/pages/Chat.tsx`:加 `ActiveModelRow`,
  Select 下拉 + 切换按钮 + "切换影响 server 全局 active model,所有会话共享"
  的提示文案(消除 per-chat 设置歧义)。
- `packages/app/src/pages/Dashboard.tsx`:`ActiveModelCard` 简化成只读 ——
  展示 active / available / registered types,底部加 "在 Chat 页切换 model →"
  链接。删除 `pendingChoice` / `switchState` / `runSwitch`、移除 Select 控件 import。
- pyproject + `chariot/__init__` 升 `0.2.2 → 0.2.3`。
- **验证**:`bun run --filter=@chariot/app build` 通过;Python 全套保持全绿
  (本步纯前端)。

---

## 0.2.4 patch

### ✅ S.1 anthropic api_key 支持配置文件直填

- 0.2.0 ~ 0.2.3 anthropic model 的 api_key 只能从环境变量取(`api_key_env`
  指向的 env)。某些场景(打包给同事用 / 多 key 切换 / Windows 用户怕设
  env)会希望直接写在配置文件里。本步加 `[models.options].api_key`
  字段;两条来源同时给 → inline `api_key` 优先,env 兜底。
- `chariot/server/model/anthropic.py`:`from_config` 抽出
  `_resolve_api_key(options)` static 方法,优先级 inline → env;两条都拿不到
  非空值 → ConfigError 文案点明"配置里没填,env 也未设"。校验空串 / 非字符串
  inline 值。`upstream_auth_failed` 错误文案同步改成"检查 config 里的
  api_key / api_key_env"。
- `chariot/config.example.toml`:模板加 (a) 直填 / (b) env 二选一说明 +
  ⚠️ 安全警告(直填模式务必把 `~/.chariot/config.toml` 排除在版本库 / 备份
  / 同步之外)。
- `tests/server/test_model_anthropic.py`:加
  `test_from_config_inline_api_key`、
  `test_from_config_inline_api_key_takes_priority_over_env`(验证 httpx
  client header 落到 inline 值)、`test_from_config_empty_inline_api_key_raises`、
  `test_from_config_non_string_inline_api_key_raises`。原 4 个 env 路径测试不动。
- `docs/DESIGN.md` §6.2 / §10:schema 与代码示例同步加 `api_key` 字段。
- `README.md`:"接真实 Anthropic 模型"改成"二选一",错误码段同步;
  CLI `chariot config init` 提示文案改成"填 api_key 或 export env"。
- pyproject + `chariot/__init__` 升 `0.2.3 → 0.2.4`。
- **验证**:`uv run ruff check .` / `uv run ruff format --check .` /
  `uv run pyright chariot/`(0 errors)/ `uv run pytest -q`
  (140 passed, 2 skipped)全绿。

---

## 0.2.5 patch

### ✅ S.2 模型连通性探针

- 用户配 `[[models]]` 之后,真正能不能通(api_key 对不对、上游可达不、网关
  header 配齐没)在没发第一条消息前完全不知道,只能"发一条然后看错码"。本步
  加显式探针:对任意 entry 跑 1 条最小 messages 请求(`max_tokens=1`),返
  `{ok, latency_ms, error?}`。
- **新增** `chariot/server/service/model_prober.py`:`ModelProber.probe(entry)`
  classmethod,临时 `ModelRegistry.build(entry)` + 发探针 body
  (`messages=[{role:"user",content:"ping"}], max_tokens=1`)。永不 raise
  —— build 失败包成 `code=config_error`、`ServiceError` 透传 code/message、
  其它异常兜底成 `probe_internal_error`。`ProbeResult` / `ProbeError` Pydantic。
- **后端路由** `chariot/server/controller/models.py`:加
  `POST /admin/models/{name}/probe`;name 不在 config.models 里 → 404
  `model_not_found`。**不副作用** —— 不改 active、不写 logs 流水(避免污染
  统计)。
- **SDK** `chariot/sdk/client.py`:加 `ProxyClient.probe_model(name)`,
  `_PROBE_TIMEOUT=60s connect=10s`(比 admin 宽,但比 chat 短)。
- **CLI** `chariot/cli/commands/model.py`:加 `chariot model probe <name>`,
  输出 `✓ name OK (Nms)` 或 `✗ name FAIL (Nms) [code] message`(失败 exit≠0)。
- **前端**:模型管理独立成 `Models` tab(`/models` 路由),不再挤在 Dashboard。
  - 新建 `packages/app/src/pages/Models.tsx`:列出 active + available + 已注册
    type;available 行式 `<ul>`,每行 `[Test]` 按钮 + 状态(probing / ✓ Nms /
    ✗ Nms · code,失败 hover 看完整 message)。顶部 disclaimer "Test 会真打
    上游一次,消耗 ~1 token,mock 模型零费用"
  - `Dashboard.tsx` 简化:删 ActiveModelCard / ProbeRow / ProbeStatus,删
    `api.listModels()` 调用与 modelsState;保留 server status grid;底部加
    "模型管理 →" 跳转 `/models`
  - `routes.tsx` 加 `/models` Route + NAV_ITEMS 项,Nav 自动展开
  - `api.ts` 加 `probeModel` + `ProbeResult` / `ProbeError` interface
- **测试**:
  - 新增 `tests/server/test_model_prober.py`:6 用例覆盖 mock 直 ok / unknown
    type config_error / anthropic 缺 key / ServiceError 透传 / 兜底 / 正常
    fake model
  - `tests/server/test_admin_models.py` 加 3 用例:probe mock ok / 未知 name
    404 / probe 不影响 active
- **不在范围**:Chat 页探针 UI(管理操作放 Dashboard 即可);probe 入 logs
  流水(0.2.5 显式跳过,避免污染 `/admin/stats`)。
- pyproject + `chariot/__init__` 升 `0.2.4 → 0.2.5`。
- **验证**:`uv run ruff check .` / `format` / `pyright chariot/` /
  `pytest -q`;`bun run --filter=@chariot/app build`(tsc + vite)全绿。

---

## 0.2.6 patch

### ✅ S.3 Chat 页高级采样参数 UI

- 0.2.5 之前 Chat 页只能调 `max_tokens`(还是个写死的常量 1024);
  `temperature` / `top_p` 完全没暴露,用户没办法做"更确定 / 更发散"这类
  常见调节。本步加可视化采样参数面板。
- **纯前端**,不动 server —— AnthropicModel 是协议透传,这些字段在 body 里
  自然透到上游。
- `packages/app/src/lib/chat.ts`:`ChatTurnOpts` 加可选 `temperature` /
  `topP`;`runTurn` 拼 body 时只在 ≠ 1 时附,遵循 Anthropic 文档"两者建议
  二选一"的语义,默认 1.0 时不污染 body。
- `packages/app/src/pages/Chat.tsx`:
  - 删掉 `MAX_TOKENS = 1024` 常量,改成 `AdvancedParams` state(temperature
    1.0 / top_p 1.0 / max_tokens 1024,与 Anthropic 默认对齐)
  - 新建 `AdvancedParamsPanel` 折叠组件(`<details>`):折叠时摘要
    `T=1 · top_p=1 · max=1024`,改过非默认值时尾部加"已改"指示
  - 展开后 3 行 `ParamRow`:label / range 滑杆 / number 输入(step 0.05
    或 1);浮点用 `round2` 防滑杆累积误差(0.30000000000000004 之类)
  - 底部说明 + Reset 按钮(已是默认时禁用)
  - inFlight 时整体禁用面板,避免改值 race 已发请求
  - 状态在 tab 内 useState 持有,刷新丢失;localStorage 持久化等真有人提
    需求再加(简化 design)
- **不在范围**:server 端校验(参数非法值现在靠上游报 400 透传,UI 上
  range/number 已经有 min/max 兜底);per-model 默认值预设(0.3.0+
  考虑)。
- pyproject + `chariot/__init__` 升 `0.2.5 → 0.2.6`。
- **验证**:Python 全套(0.2.5 改的后端不动)+ `bun run --filter=@chariot/app build`
  (tsc 严格类型 + vite)。

---

## 0.3.0+ 路标

详见 `docs/ROADMAP.md`。本表只到 0.2.6 收尾。
