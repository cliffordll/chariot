# Chariot 0.7.0 推进表

> **当前活跃**:`0.7.0`(开发中)
> **上一版归档**:[`docs/history/0.6.5/FEATURE.md`](history/0.6.5/FEATURE.md)
>
> **0.7.0 主题**:OpenAIProvider + Memory + Skills(自演化基础)。详见
> [`ROADMAP.md`](ROADMAP.md) v2 章节;具体步骤待启动时落地。

每步推进规则(沿用):每完成一步 → 跑「验收」全条目 → 等用户确认"通过"再标 ✅,
然后 commit。一个 FEATURE 步骤 = 一个 commit。

---

## 验收标准统一格式

每步用以下 5 个维度判收(下文每步「验收」段照此填具体内容):

- **单测**:本步新增 / 改写的测试,关键断言要点
- **静态**:`uv run ruff check .` + `uv run ruff format --check .` +
  `uv run pyright chariot/` 三件套全绿,**0 警告 0 错误**为基线
- **手测**:具体命令 + **预期看到的输出形态**
- **回归**:旧行为不变,通过 `grep` 守不应残留的引用
- **不通过特征**:出什么形态就算挂了,作为 review 时的红旗清单

---

## 0.7.0 patch 列表

(待补)
