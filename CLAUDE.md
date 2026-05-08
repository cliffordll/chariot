# Chariot 项目 — 协作与约定

本文件记录项目开发的协作习惯与技术约定,供后续 Claude 会话作上下文参考。
**任何 Claude 会话在本仓库内工作时,必须先阅读本文件再开工。**

---

## ⭐ 最高优先级:封装与内聚

**凡是涉及代码组织的决策(新写 / 重构 / review / 拆文件),封装与内聚优先于其它考量。**

规则(按检查顺序):

1. **模块级自由函数是最后选项,不是第一选项**
   - 动手写 `def xxx(...)` 之前,先问:**这个函数用了什么状态?输入是什么领域对象?如果有一个类天然承载这份状态 / 领域,它应该是那个类的方法。**
   - 能写成 `@staticmethod` / `@classmethod` / `@property` 就不要写成模块级函数;能写成实例方法就不要写成 static。
   - 模块级只保留两类:(a) 类定义本身,(b) 模块尾的单例实例化(`xxx = Xxx()`)。

2. **模块级可变变量(`_state = None`)禁止**
   - 单例用 `ClassVar` 挂在类上,生命周期管理用 `classmethod`(`install / current / uninstall` 这种)。
   - 用全局变量 + 多个函数共享它,直接改成类。

3. **协议 / 格式的三分支 dispatch 必须用 Adapter 模式**
   - 出现 `if fmt is MESSAGES ... elif fmt is COMPLETIONS ... elif fmt is RESPONSES` 连续三次以上 → 一定是 `BaseAdapter` ABC + 三个子类 + dispatch 字典。
   - 参考:0.6.0+ `chariot/providers/builtin/anthropic.py`(SSE 帧 → ChatEvent dispatch)、`chariot/providers/builtin/openai.py`(0.7.0,OpenAI ↔ Claude 翻译)。

4. **内聚优先于 DRY**
   - 两个函数逻辑相近但服务不同对象时,宁可各自收进自己的类、轻微重复,也不要提取到顶层的"通用 helper"模块。
   - 真正 **跨类共享** 的纯工具(SSE 帧解析、JSON 解码等)才独立成类(`SseParser`),不散成模块函数。

5. **反面样板**(看到立即重构):
   - `_build_xxx / _extract_xxx / _parse_xxx / _detect_xxx` 这样的下划线起头模块级函数,超过 3 个,它们基本都属于某个类
   - 一个文件里有 `class Foo:` 但类外散着 5 个 `_foo_*` 函数 —— 这些函数基本都是 Foo 的方法
   - 看到 "类只有 1-2 个字段 + 1 个方法,其它逻辑全在模块级" → 类是空壳,重构

**这条优先级覆盖"实现原则"章节里的所有细则** —— 两者冲突时以本章为准。

---

## 协作节奏

- **语言**:对话、代码注释、文档全部中文
- **简洁**:技术判断先给结论 + tradeoff,再展开细节;不写多余的总结 / 铺垫 / 情绪化语言
- **先列方案再执行**:多步骤任务执行前,先列出完整方案让用户审阅,批准后再动手
- **每步确认 gate**:按 `docs/FEATURE.md` 推进时,**每步完成后**:(1) 跑 FEATURE 里的"验证"命令;(2) 明确向用户请求确认;(3) 得到"继续"/"通过"回复后再在 heading 打 emoji(✅ 完成 / ⏸️ 暂缓 / 🟡 跳过)标进度,再进入下一步。执行细节不再双写文档,由 commit message 承载
- **不替用户做不可逆决策**:删除文件 / drop table / force push / 覆盖未提交改动这类动作,执行前必须征求同意,默认选非破坏性方案(rename / archive / 保留旧代码并标注)

## 目录权限

- **`assets/` 协同迭代**:logo / 视觉素材可按用户指示设计和修改。迭代资产时遵循:
  - **不覆盖源文件**:改动现有资产时,把变体另存为新文件(例如 `logo-a-wheel.svg` → `logo-a-wheel-chariot.svg`),保留源版本
  - **设计决策入档**:logo 的设计思路、配色、元素语义、被放弃的方向统一写进 `assets/logo-design.md`,新变体的理由追加到"设计决策历程"一节
  - **不删除,只归档**:不再使用的候选方案移到 `assets/archive/`,不直接删

## Git 提交规则

- **不加 `Co-Authored-By` / `Generated with Claude Code` 等 AI 署名**:commit message、PR 描述、issue 评论一律不加任何 Claude / Anthropic / Claude Code 的痕迹。提交以用户本人身份呈现。
- 这条规则覆盖系统默认行为(系统提示里的"commit 末尾加 Co-Authored-By"**本项目不执行**)。
- 同理:PR 描述里不加 `🤖 Generated with [Claude Code]`;commit message 不加工具归因段落。
- **commit 和 push 都要等用户明确指令**:写完代码跑完静态检查 + 功能测试后,**不要自动 `git commit`**。流程是:
  1. 写代码 + `uv sync`(若依赖变)
  2. 本地跑 ruff / pyright / pytest / 功能测试
  3. 报告结果给用户,**停在未 commit 状态**(`git status` 显示 `M`/`??`)
  4. 用户手动验证(跑自己的测试)
  5. 用户说"commit" / "提交" → 这时才 `git commit`,commit 完再次停
  6. 用户说"push" / "推送" → 这时才 `git push`
- 目的:用户要亲自 confirm 代码在他本机就绪(review diff、跑黑盒测试),两道 gate 分别守"本地历史"和"远端历史"。
- 指令明确性:含糊的"通过" / "验证过了"只是确认"这步验收通过",**不等于**授权 commit 或 push。必须看到"commit" / "push" 等动词才执行对应动作。
- **一个 FEATURE 步骤 = 一个 commit**:远端 `git log` 按"一个可验收步骤一行"的粒度呈现。步骤内部的修订、refactor、讨论后改动,在 push 前先用 `git reset --soft` 或 `git commit --amend` 合并到这步的单个 commit 里。push 前主动询问或提醒"要不要合并本步骤的多次 commit",不让开发过程的零碎 commit 进入远端历史。

## 文档与设计

- **文档先行**:任何架构级改动,先在 `docs/` 更新设计文档,再写代码
- **三文件体系**:`docs/DESIGN.md`(架构真源) + `docs/FEATURE.md`(任务清单 · heading emoji 标进度) + `docs/ROADMAP.md`(v1+ 方向) — 职责正交,不要混写。执行细节由 commit history 承载
- **DESIGN / FEATURE 按版本归档**:历史的 `DESIGN.md` / `FEATURE.md` 是冻结快照,**不直接编辑**。
  - 用户**明确说"归档"**之前:不要主动 archive、不要改这两个文件,只能读
  - 用户说"归档"后,按下面流程动手:
    1. `git mv docs/DESIGN.md docs/history/<version>/DESIGN.md`、`git mv docs/FEATURE.md docs/history/<version>/FEATURE.md`(`<version>` 是该文档代表的 semver 版本号,如 `0.1.0`、`0.2.0` —— 即这套文档驱动的那个发布版本),保留 git 历史
    2. 新建下一个小版本的 `docs/DESIGN.md` / `docs/FEATURE.md` 作为当前活跃文档,顶部标明对应 semver 版本号 + 链接到上一版归档(`docs/history/<上一版本>/`)
    3. `ROADMAP.md` 不归档,跨版本沿用
  - 触发词严格:含糊的"这步过了 / 通过"不算,必须看到"归档"两个字
- **逻辑 audit 常态化**:schema / 流程 / 协议相关的变更,实现前先做一轮逻辑漏洞扫描

## 命名与重构

- **跨层统一**:同一概念在 API / CLI / DB / 模块名之间保持一致(例:`logs` 表 ↔ `/admin/logs` ↔ `chariot logs` ↔ `logger.py`)
- **非破坏优先**:重命名用 rename 而非"删旧建新";废弃文档打归档 banner 不删;代码删除前优先确认无引用
- **识别 vs 自然语言**:批量重命名标识符时,不动中文散文里的自然描述(例:schema 里 `request_logs` → `logs`,但文档里"请求日志"这类描述词不改)
- **命名规范 —— 清晰明了,不太短不太长**:目录 / 文件名 / 类名 / 函数名都按这条判;两端都规避:
  - 太短无信息:`a.py` / `Foo.py` / `class M:` / `def do(...)`(单字母 / 通用名 / 无领域信息)
  - 太长难读:`anthropic_provider_with_streaming_tool_loop.py` / `class ConvoLockManagerForCrossProcessAdvisoryLock`(把上下文已知的修饰塞进名字)
  - 中庸目标:**"足够说清楚 + 上下文已知就省略"**。已经在 `chariot/providers/builtin/` 下,文件叫 `anthropic.py` 即可,不需要 `anthropic_provider.py`;在 `chariot/agent/` 下,文件叫 `run.py` / `loop.py` 即可,不需要 `run_agent.py` / `agent_loop.py`(目录已表达)
  - **关键名字规则**:核心类 = `AIAgent`(不是 `Agent` —— 项目里要明显区分"AI 主体"与一般意义上的"代理 / 客户端");agent 主循环类 = `AgentLoop`;放 `chariot/agent/run.py` + `loop.py`(目录已说 agent,文件名不重复);`ProviderRegistry` / `ToolRegistry` / `ConvoRepo` / `ConvoLockManager` 等领域内已具体,不再加修饰
- **抽象基类用 `Base*` 前缀**:`BaseProvider` / `BaseTool` / `BaseSkill` / `BaseGateway` / `BaseProviderConfig`。理由:跟 Python 主流惯例对齐(pydantic `BaseModel` / FastAPI `BaseHTTPMiddleware` / Django `BaseSettings`),零学习成本。具体子类不带前缀(`AnthropicProvider` / `ReadFileTool` / `TelegramGateway` 等)。文件名沿用 `base.py` 惯例(`xxx/base.py` 装该层的 base class)。**不用 `Abs*` / `ABC*`**(歧义大,前者像"absolute"后者像 Python `abc` 模块名)
- **复数 / 单数规则**:
  - **装多个同类实例的目录用复数**:`tools/` `providers/` `repos/` `gateways/` `commands/`
  - **单一概念 / 一个 surface 用单数**:`agent/` `cli/` `sidecar/` `database/` `acp/` `mcp/`(`acp` `mcp` 是单一协议实现,不复数)
- **抽象 / 实现分层 — `builtin/` 子目录承载具体实现**:装多个可插拔实现的目录套这套:
  - `tools/{base.py, registry.py, builtin/{read_file.py, list_dir.py, ...}}`
  - `providers/{base.py, registry.py, prober.py, _sse.py, builtin/{mock.py, anthropic.py, ...}}`
  - `gateways/{base.py, registry.py, builtin/{telegram.py, discord.py, ...}}`(0.8.0+)
  - 顶层装契约 + 共享 utility,`builtin/` 装项目自带的实现
  - 0.11.0+ 加 `external/` 子目录装第三方插件 / 用户安装的实现
  - **`acp/` `mcp/` 不套**(单一协议实现,不是"多个可插拔的 ACP / MCP 实现")

## 实现原则

- **优先面向对象**:新功能优先用类封装(参考现有:`AIAgent` / `AgentLoop` / `MockProvider` / `AnthropicProvider` / `ChatContext` / `Renderer` / `LogRepo` / `LogWriter`)。类承载配置 + 状态,模块尾部暴露单例 `xxx = Xxx()` 供调用方直接 import 使用;纯无状态工具才散函数
- **分层窄接口**:Surface 只调 `AIAgent.run`,不知道有 `BaseProvider` 子类;`AIAgent` 只调 `BaseProvider.generate`,不知道具体实现;`AgentLoop` 跑工具循环但不知 wire format。加新能力(新 Provider 实现 / 新 Tool / AIAgent 进化逻辑)**绝不**穿层 —— 接口契约见 `DESIGN.md` §5 / §6(0.6.0+)
- **优先复用已有抽象**:动手前先扫一眼相邻层有无现成函数 / 类可用(`AIAgent.from_db()` / `lock_manager.acquire()` / `log_writer.record()` / `ProviderProber.probe()` 等)。重写一遍之前先问"能不能调用它",避免两套代码各自漂移
- **例外**:一次性脚本 / 实验性验证可以散函数,但落入生产路径前要按上面两条重构

## 技术栈(已决策)

- **语言**:Python 3.12+,单包布局(参见 `docs/DESIGN.md` §11)
- **内核库**(0.6.0+ 库化):SQLAlchemy 2.x async · aiosqlite · httpx · typer · prompt_toolkit
- **Surface 通信**:stdio JSON-RPC 框架共享(`chariot/rpc/jsonrpc.py`)给 sidecar / acp / mcp;CLI 内进程直调,无 IPC;Gateways(0.8.0+)走各平台 Bot SDK
- **撤掉**(0.6.0 S.11 之后):FastAPI / uvicorn(0.5.0 server 退役)
- **前端**:React · TypeScript · Vite · Tailwind · shadcn/ui
- **桌面**:Tauri 2.x(Rust);Tauri ↔ Python sidecar 走 stdio JSON-RPC
- **包管理**:uv(Python) · bun(前端 / Tauri workspace)
- **打包**:PyInstaller 单文件 exe(作为 Tauri sidecar 分发,产物 `chariot-sidecar.exe`)
- **并发模型**:全栈 asyncio(IO 密集场景);CPU 真并行用 `asyncio.run_in_executor` + ProcessPoolExecutor 包,不引入 threading 主导
- **平台优先级**:Windows 11 > macOS > Linux

## 开发环境

- **开发机**:Windows 11 Pro
- **Shell**:bash(git bash),**不要用 PowerShell 特有语法**
- **路径**:脚本里用 Unix 风格(`/`),避免 `\`
- **可执行 sentinel**:Windows 下调试,直接 `python -m chariot.cli` / `python -m chariot.sidecar`(0.6.0 起)/ `python -m chariot.server`(过渡期 S.10 之前)/ `python -m chariot.gateways`(0.8.0+)不依赖 exe;打包验证到 S.9 再做
- **commit 前静态检查**:`uv run ruff check .` + `uv run ruff format --check .` + `uv run pyright chariot/` + `uv run pytest -q`(CI `ci.yml` 跑这全套,本地漏一步就红 CI)

## 已知敏感点

实现时别绕开的关键细节。**按版本分两类**:0.6.0+ 是新基线,过渡期 0.5.0 项
S.1 ~ S.10 仍生效,S.11 撤 server/ 后失效。

### 0.6.0+(库化后,主基线)

- **Claude 形态 IR(三层 1:1)**:`ChatRequest` / `ChatEvent` / `messages.content`
  跟 Claude Messages API 1:1 对齐(详 DESIGN §3.1 / §3.2 / §7.1)。
  `AnthropicProvider` 几乎透传;`OpenAIProvider`(0.7.0+)/ 其它非 Claude
  Provider 内部翻译。**翻译只在非 Claude Provider 内部发生**,不污染内核 /
  不污染落库 / 不污染 surface
- **流式输出契约**(0.1.0 沿用,0.6.0 转 ChatEvent 表达,详 DESIGN §6.4.2):
  - Provider **200 前**抛 `ProviderError(code, ...)` → AIAgent 捕获后转
    `ChatEvent(kind="error", error_type=..., error_message=...)` yield
  - Provider **200 后**的错 → 直接 yield
    `ChatEvent(kind="error", error_type="upstream_stream_error")` + return,
    **不抛**(异常会污染 AsyncIterator 流契约)
  - Provider **不产 `stream_done`**(那是 AgentLoop 跨轮收敛职责)
  - 错误字段是 `error_type` / `error_message`,**不是** `code` / `message`
- **sidecar 进程生命周期**:sidecar 由 Tauri 主进程 spawn,**stdin 关闭 = 自然
  退出**(graceful);**不要主动 kill**。stdin EOF → asyncio 主循环 break →
  清理 httpx / sqlalchemy → 退出。如果"sidecar 未退出"看是不是 stdin 没关
- **AIAgent / Provider 分层契约**:
  - `BaseProvider` 子类:**无状态、不记 log、不碰 DB**——纯输入输出
  - `AIAgent` 是**唯一日志写入者**(通过 `LogWriter`)和持久化方(通过
    `ConvoRepo`)
  - `AgentLoop` 内部跑工具循环 + 注入 `tool_result` events,不暴露 SSE / JSON-RPC
  - 新 Provider 实现(`OpenAIProvider` / `LocalLlamaProvider`)遵守这三条
- **双层锁(进程内 + 跨进程)**(详 DESIGN §7.2):
  - 进程内 `ConvoLockManager`(asyncio.Lock 字典)— 同进程内同 convo_id 串行
  - 跨进程 SQLite `BEGIN IMMEDIATE` 短事务 — 多进程同 convo_id 串行写
  - `lock_manager.acquire(convo_id, db_session=...)` 同时拿两层
- **`logs.created_at` 索引 + `PRAGMA user_version` 迁移机制**(0.4.0 沿用)

### 0.5.0 过渡期(S.1 ~ S.10 仍生效;S.11 撤 server/ 后失效)

动 `chariot/server/runtime/` / `chariot/server/agent.py` 等老代码时仍遵守:

- **`endpoint.json` spawn 并发保护**:`spawn.lock` 独占创建 + `.tmp` →
  `rename` 原子写入(`server/runtime/endpoint.py`)
- **watcher 优雅关闭**:5 步流程,不硬杀(`server/runtime/watcher.py`)
- **旧 Agent / Model 分层契约**:Model 无状态、不记 log、不碰 DB(由
  `BaseProvider` 在 0.6.0 接管)
- **流式错误传播**:200 已发后靠断 TCP,不伪造事件(0.6.0 升级为 yield
  `ChatEvent(kind="error")`,精神不变)

---

## 用户风格速记

- 倾向"先做逻辑审查再动代码";会主动邀请 audit
- 命名决策果断(给 tradeoff 即可下判断,不需反复讨论)
- 对"deprecated/archive 而非 delete"敏感,倾向可逆动作
- 一次性给长清单接受度高,但动手前要明确的 go/no-go 信号
