# Chariot 0.3.0 推进表

> **当前活跃**:`0.3.0`(开发中)
> **上一版归档**:[`docs/history/0.2.6/FEATURE.md`](history/0.2.6/FEATURE.md)
>
> **0.3.0 主题**:模型配置从 TOML 文件迁到 SQLite,Models 页加 CRUD + Duplicate。
> 详细架构见 [`DESIGN.md`](DESIGN.md) §6。

每步推进规则(沿用):每完成一步 → 跑验证 → 等用户确认"通过"再标 ✅,然后 commit。

---

## 0.3.0 patch 列表

### 🔲 D.1 DB schema + ModelRepo + Agent 接 DB

- migration v2:建 `models` / `settings` 表;`ModelRepo.seed_if_empty` 首次跑写入 `('mock', 'mock', '{}')` + active=mock
- ORM 类 `ModelRow` / `SettingRow`(`chariot/server/database/models.py`)
- `ModelRepo`(`chariot/server/repository/model_repo.py`):list / get / create / update / delete / duplicate / get_active / set_active
- `ChariotConfig.from_db(session)` classmethod;`ConfigLoader` 删除
- `Agent.install_from_db(config)` 替代 `install_from_config`
- lifespan 改:跑 migration → seed → ChariotConfig.from_db → Agent.install_from_db
- 新建 `tests/server/test_model_repo.py`;旧 `tests/server/test_config.py` 改写
- **验证**:Python 全套 + server 启动 smoke

### 🔲 D.2 admin API entries CRUD

- `controller/models.py` 加 4 路由(POST/PUT/DELETE/POST duplicate)+ Pydantic schema
- `POST /admin/models`(切 active)改成持久化到 `settings`
- update active entry → 触发 active model rebuild
- duplicate 命名规则:body.as 缺省 `<name>_copy`,碰撞 `_copy_2` / `_3`
- 错误码:`name_exists` 409 / `unknown_type` 400 / `cannot_delete_active` 400 / `model_not_found` 404
- 扩展 `tests/server/test_admin_models.py`
- **验证**:Python 全套

### 🔲 D.3 SDK + CLI add/edit/rm/duplicate

- `ProxyClient` 加 5 方法:`create_model / update_model / delete_model / duplicate_model`(get 复用 list)
- CLI `chariot model add / edit / rm / duplicate` 子命令(`list` / `use` / `probe` 沿用)
- **删除** `chariot/cli/commands/config.py` 整文件 + 解除注册
- **删除** `chariot/config.example.toml`(模板 obsolete)
- **验证**:Python 全套 + 黑盒 CLI

### 🔲 D.4 Models 页 CRUD UI + Duplicate

- `Models.tsx` 大改:每行加 [Edit] / [Dup] / [Del] 按钮;顶部 [+ Add] 按钮
- `AddEditDialog` 组件:type 下拉 → 按 type 切换字段表单(anthropic = model_id / api_key / api_key_env / base_url;mock = 无 options)
- Delete 走 confirm dialog;active entry 提示先切走
- Duplicate 弹 Dialog 字段预填,name 默认 `<name>_copy`
- `api.ts` 加 `createModel / updateModel / deleteModel / duplicateModel`
- 切 active 仍在 Chat 页(决策 7A);Models 只读展示当前 active + 跳转链
- **验证**:`bun run --filter=@chariot/app build`

### 🔲 D.5 README + 残余清理

- `README.md` 删 `chariot config init/show` 相关段落,加"在 Models 页加 model" 说明
- `README.md` "接真实 Anthropic 模型"改成"启动 server → 浏览器开 Models 页 → [+ Add] 填字段"流程
- 检查所有引用 `~/.chariot/config.toml` 的文档,改成"由 Models 页管理"
- DESIGN.md §6 / §8 终稿(实现期间发现的细节回填)

### 🔲 Z.1 0.3.0 收尾

- pyproject + `chariot/__init__` 升 `0.2.6 → 0.3.0`
- FEATURE.md 全部 D.x 标 ✅,heading 改 "## 0.3.0 patch 列表"
- 全套验证(ruff / format / pyright / pytest / bun build)
- commit + 等用户 push

---

## 0.4.0+ 路标

详见 `docs/ROADMAP.md`。要点:

- **0.4.0**:Agent 层多轮记忆 + 工具调用
- **0.5.0**:进化循环

本表只到 0.3.0 收尾。
