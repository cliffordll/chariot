# Chariot 0.3.1 推进表

> **当前活跃**:`0.3.1`(开发中)
> **上一版归档**:[`docs/history/0.2.6/FEATURE.md`](history/0.2.6/FEATURE.md)
>
> **0.3.1 主题**:路由模型重构 —— active 概念删除,client 在 body.model 写
> entry name 路由;`models` 表加 `params` 列承载 runtime sampling 默认值,
> Models 页可编辑。详细架构见 [`DESIGN.md`](DESIGN.md) §6。
>
> 上一版 0.3.0 的 patch 见 git log(D.1 ~ Z.1 已全 ✅,不归档,沿用本文件作为
> 0.3.x 系列推进表)。

每步推进规则(沿用):每完成一步 → 跑验证 → 等用户确认"通过"再标 ✅,然后 commit。

---

## 0.3.0 patch 列表

### ✅ D.1 DB schema + ModelRepo + Agent 接 DB

- migration v2:建 `models` / `settings` 表;`ModelRepo.seed_if_empty` 首次跑写入 `('mock', 'mock', '{}')` + active=mock
- ORM 类 `ModelRow` / `SettingRow`(`chariot/server/database/models.py`)
- `ModelRepo`(`chariot/server/repository/model_repo.py`):list / get / create / update / delete / duplicate / get_active / set_active
- `ChariotConfig.from_db(session)` classmethod;`ConfigLoader` 删除
- `Agent.install_from_db(config)` 替代 `install_from_config`
- lifespan 改:跑 migration → seed → ChariotConfig.from_db → Agent.install_from_db
- 新建 `tests/server/test_model_repo.py`;旧 `tests/server/test_config.py` 改写

### ✅ D.2 admin API entries CRUD

- `controller/models.py` 加 4 路由(POST/PUT/DELETE/POST duplicate)+ Pydantic schema
- `POST /admin/models`(切 active)改成持久化到 `settings`
- update active entry → 触发 active model rebuild
- duplicate 命名规则:body.as 缺省 `<name>_copy`,碰撞 `_copy_2` / `_3`
- 错误码:`name_exists` 409 / `unknown_type` 400 / `cannot_delete_active` 400 / `model_not_found` 404

### ✅ D.3 SDK + CLI add/edit/rm/duplicate

- `ProxyClient` 加 5 方法
- CLI `chariot model add / edit / rm / duplicate` 子命令
- **删除** `chariot/cli/commands/config.py` 整文件 + 解除注册
- **删除** `chariot/config.example.toml`

### ✅ D.4 Models 页 CRUD UI + Duplicate

- `Models.tsx` 大改:每行加 [Edit] / [Dup] / [Del] 按钮;顶部 [+ Add] 按钮
- `AddEditDialog` 组件
- Delete 走 confirm dialog
- Duplicate 弹 Dialog 字段预填
- `api.ts` 加 `createModel / updateModel / deleteModel / duplicateModel`

### ✅ D.5 README + 残余清理

- README 删 `chariot config init/show` 段落,加 Models 页流程
- 文档统一改"由 Models 页管理"

### ✅ Z.1 0.3.0 收尾

- pyproject + `chariot/__init__` 升 `0.2.6 → 0.3.0`

---

## 0.3.1 patch 列表(路由模型重构)

### ✅ R.1-R.3 server 路由重构(合并)

- migration v3:`ALTER TABLE models ADD COLUMN params TEXT NOT NULL DEFAULT '{}'` + `DROP TABLE settings`
- ORM:`ModelRow.params` 字段 / 删 `SettingRow`
- `ModelRepo`:删 `get_active / set_active / clear_active`,加 `params` 读写;
  `seed_if_empty` 不再写 active
- `ChariotConfig`:删 `active` 字段,改纯 entries 容器;`ModelEntry` 加 `params: dict[str, Any]`(默认空 dict)
- `Agent`:删 `_active_name / active_name / switch_to / install_from_config(active 部分)`;
  改 `models: dict[str, Model]` 字典 + `handle()` 按 body.model 路由 + `unknown_model_name` 400
- `controller/models.py`:删 `POST /admin/models` 切 active 端点;`GET /admin/models` 删 `active` 字段;
  EntryResponse / Create / Update Request 加 `params`
- `controller/runtime.py`:`StatusResponse.model` → `entries_count`
- 测试更新:`test_admin_models / test_agent / test_app_lifespan / test_config / test_model_repo / test_dataplane`
- **验证**:`pytest -q` + `ruff check` + `pyright`

### ✅ R.4 SDK + CLI 同步

- SDK:删 `use_model / SwitchModelResponse`;`create_model / update_model` 接 params
- CLI:删 `chariot model use`;`add / edit` 加 `-p key=value`(可重复);`list` 不再标 active
- 测试更新:`test_commands / test_client_admin`
- **验证**:`pytest -q`

### ✅ R.5 Chat 页极简化(entry 选择 + localStorage)

- `api.ts`:删 `useModel / SwitchModelResponse / ModelsListResponse.active`;`ModelEntry` 加 `params`;
  `StatusResponse.model` → `entries_count`
- `Chat.tsx`:删 `ActiveModelRow`(切 active 那一栏)+ **删 `AdvancedParamsPanel`** + 删 `AdvancedParams` state;
  新 `EntryRow`(Select + localStorage 持久化 `chariot.chat.selected_entry`)
- 发请求时直接从当前 entry 的 `params` 现取 sampling(`max_tokens` 兜底 1024;
  `temperature` / `top_p` 缺失就不传)。改 entry params 在 Models 页下次发消息立即生效;
  Chat 页不再展示 sampling 配置 UI
- `Dashboard.tsx`:`status.model` → `status.entries_count`
- **验证**:`bun run --filter=@chariot/app build`

### ✅ R.6 Models 页折叠展开 + ParamsEditor + 预设字段

- `Models.tsx` 行头只剩 [▾▸] name [type] + 操作按钮
- 行展开区:`OptionsBlock`(只读展示 `model / api_key`(脱敏)/ `base_url`)+ `ParamsEditor`(可编辑 params)
- ParamsEditor:KV 列表 + 上方 `presets:` 预设 chips(temperature / top_p / top_k / max_tokens / stop_sequences),
  点击一键加该 key 一行(已存在则灰掉)
- params 值用 JSON 解析(`0.7` → number / `true` → bool / 普通文本免引号留 string)
- 删 confirm dialog 不再判 active(0.3.1 无此约束)
- **验证**:`bun run --filter=@chariot/app build`

### ✅ R.8 docs + 版本号 0.3.0 → 0.3.1

- `DESIGN.md` §1 / §2 / §6 / §8 重写到路由模型 + params 字段
- `FEATURE.md` 加 0.3.1 patch 列表
- `ROADMAP.md` 0.3.x 段补一行
- `README.md` 改 client 用法说明 + 删旧 active 段落
- pyproject + `chariot/__init__` 升 `0.3.0 → 0.3.1`
- 全套验证(ruff / format / pyright / pytest / bun build)
- commit + 等用户 push

---

## 0.4.0+ 路标

详见 `docs/ROADMAP.md`。要点:

- **0.4.0**:Agent 层多轮记忆 + 工具调用
- **0.5.0**:进化循环

本表覆盖 0.3.0 + 0.3.1 两个小版本;到 0.3.1 收尾结束。
