import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  api,
  type AuditEvent,
  type SkillCurateBuckets,
  type SkillDetail,
  type SkillSummary,
} from "@/lib/api";

type TabKey = "library" | "proposals" | "curator";

const TABS: { key: TabKey; label: string }[] = [
  { key: "library", label: "Library" },
  { key: "proposals", label: "Proposals" },
  { key: "curator", label: "Curator" },
];

export default function Skills() {
  const [tab, setTab] = useState<TabKey>("library");
  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Skills</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          B6:Skills 注册表(builtin YAML + DB 行 union)+ propose 历史 + Curator 静态建议。
        </p>
      </div>

      <div className="flex gap-1 rounded-lg border border-border p-1 w-fit">
        {TABS.map((t) => (
          <Button
            key={t.key}
            size="sm"
            variant={tab === t.key ? "default" : "ghost"}
            onClick={() => setTab(t.key)}
            className="h-7 px-3 text-xs"
          >
            {t.label}
          </Button>
        ))}
      </div>

      {tab === "library" && <LibraryPanel />}
      {tab === "proposals" && <ProposalsPanel />}
      {tab === "curator" && <CuratorPanel />}
    </section>
  );
}

// ============================================================================
// Library tab —— 全部 skill 列表 + 详情 + enable/disable/install/delete
// ============================================================================

function LibraryPanel() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [installDraft, setInstallDraft] = useState("");
  const [installErr, setInstallErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadErr(null);
    try {
      const res = await api.listSkills();
      setSkills(res.skills);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const inspect = useCallback(
    async (name: string) => {
      if (selected && selected.name === name) {
        setSelected(null);
        return;
      }
      setDetailErr(null);
      try {
        const res = await api.getSkill(name);
        setSelected(res.skill);
      } catch (e) {
        setDetailErr(e instanceof Error ? e.message : String(e));
      }
    },
    [selected],
  );

  const toggle = useCallback(
    async (skill: SkillSummary) => {
      if (skill.source === "builtin") return; // builtin 永远 enabled,UI 上不应触发
      setBusy(true);
      try {
        if (skill.enabled) {
          await api.disableSkill(skill.name);
        } else {
          await api.enableSkill(skill.name);
        }
        await load();
      } catch (e) {
        setLoadErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const remove = useCallback(
    async (skill: SkillSummary) => {
      if (skill.source === "builtin") return;
      if (!confirm(`Delete DB skill ${skill.name}?`)) return;
      setBusy(true);
      try {
        await api.deleteSkill(skill.name);
        await load();
        if (selected?.name === skill.name) setSelected(null);
      } catch (e) {
        setLoadErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [load, selected],
  );

  const installYaml = useCallback(async () => {
    if (!installDraft.trim()) return;
    setInstallErr(null);
    setBusy(true);
    try {
      await api.installSkill({ content: installDraft });
      setInstallDraft("");
      await load();
    } catch (e) {
      setInstallErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [installDraft, load]);

  const forkBuiltin = useCallback(
    async (name: string) => {
      setBusy(true);
      try {
        await api.installSkill({ from_builtin: name });
        await load();
      } catch (e) {
        setLoadErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  return (
    <div className="space-y-4">
      {loadErr && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {loadErr}
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Version</TableHead>
            <TableHead>Enabled</TableHead>
            <TableHead>Tags</TableHead>
            <TableHead>Description</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {skills.map((s) => (
            <TableRow key={`${s.source}:${s.name}`}>
              <TableCell className="font-mono text-xs">{s.name}</TableCell>
              <TableCell>
                <Badge variant={s.source === "builtin" ? "secondary" : "default"}>{s.source}</Badge>
              </TableCell>
              <TableCell className="text-xs">{s.version}</TableCell>
              <TableCell>
                <Badge variant={s.enabled ? "default" : "outline"}>
                  {s.enabled ? "yes" : "no"}
                </Badge>
              </TableCell>
              <TableCell className="text-xs">{s.tags.join(", ") || "—"}</TableCell>
              <TableCell className="text-xs text-muted-foreground max-w-md truncate">
                {s.description}
              </TableCell>
              <TableCell className="text-right">
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="outline" onClick={() => inspect(s.name)}>
                    {selected?.name === s.name ? "Hide" : "Show"}
                  </Button>
                  {s.source === "builtin" ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => forkBuiltin(s.name)}
                    >
                      Fork
                    </Button>
                  ) : (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => toggle(s)}
                      >
                        {s.enabled ? "Disable" : "Enable"}
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={busy}
                        onClick={() => remove(s)}
                      >
                        Delete
                      </Button>
                    </>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {selected && (
        <div className="rounded-lg border border-border p-4 space-y-2 bg-card/40">
          <div className="text-xs text-muted-foreground">
            <span className="font-semibold">{selected.name}</span>{" "}
            <Badge variant="secondary" className="ml-2">
              {selected.source}
            </Badge>{" "}
            <span className="ml-2">v{selected.version}</span>
          </div>
          <div className="text-sm">{selected.description}</div>
          <div className="text-xs text-muted-foreground">
            allowed_tools: {selected.allowed_tools ? selected.allowed_tools.join(", ") : "(unrestricted)"}
          </div>
          <div className="text-xs text-muted-foreground">
            forbidden_tools: {selected.forbidden_tools.join(", ") || "(none)"}
          </div>
          <pre className="text-xs whitespace-pre-wrap bg-muted/40 p-3 rounded border border-border max-h-96 overflow-auto">
            {selected.prompt}
          </pre>
          {detailErr && <div className="text-xs text-destructive">{detailErr}</div>}
        </div>
      )}

      <div className="rounded-lg border border-border p-4 space-y-2">
        <h3 className="text-sm font-semibold">Install from YAML</h3>
        <p className="text-xs text-muted-foreground">
          粘 manifest YAML(schema_version / name / description / prompt 必填),装到 DB(默认 enabled)。
        </p>
        <Textarea
          rows={6}
          value={installDraft}
          onChange={(e) => setInstallDraft(e.target.value)}
          placeholder="schema_version: 1&#10;name: my_skill&#10;description: ...&#10;prompt: ..."
          className="font-mono text-xs"
        />
        <div className="flex justify-between items-center">
          <div className="text-xs text-destructive">{installErr}</div>
          <Button size="sm" disabled={busy || !installDraft.trim()} onClick={installYaml}>
            Install
          </Button>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Proposals tab —— 历史 propose_skill 调用(audit_events filter source='propose')
// ============================================================================

function ProposalsPanel() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadErr(null);
    try {
      // 多取一点,客户端 filter
      const res = await api.listAuditEvents({ limit: 200 });
      const filtered = res.events.filter(
        (ev) => ev.event_type === "skill_store" && ev.payload.source === "propose",
      );
      setEvents(filtered);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <p className="text-sm text-muted-foreground">
          Agent 自发提议的 skill 落库历史。propose 默认 enabled=False —— 在 Library tab 手工启用。
        </p>
        <Button size="sm" variant="outline" onClick={refresh}>
          Refresh
        </Button>
      </div>
      {loadErr && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {loadErr}
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Created</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Name</TableHead>
            <TableHead>Proposer</TableHead>
            <TableHead>Checkpoint</TableHead>
            <TableHead>Error</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {events.map((ev) => {
            const payload = ev.payload as Record<string, unknown>;
            const name = (payload.name as string | undefined) ?? "—";
            const proposer = (payload.proposer as string | undefined) ?? "—";
            const checkpointId = (payload.checkpoint_id as string | undefined) ?? null;
            const error = (payload.error as string | undefined) ?? "";
            return (
              <TableRow key={ev.id}>
                <TableCell className="text-xs">
                  {new Date(ev.created_at).toLocaleString()}
                </TableCell>
                <TableCell>
                  <Badge variant={ev.status === "create" ? "default" : "destructive"}>
                    {ev.status ?? "—"}
                  </Badge>
                </TableCell>
                <TableCell className="font-mono text-xs">{name}</TableCell>
                <TableCell className="text-xs">{proposer}</TableCell>
                <TableCell className="text-xs">
                  {checkpointId ? (
                    <a className="underline" href={`/security#checkpoints`}>
                      {checkpointId.slice(0, 12)}
                    </a>
                  ) : (
                    "—"
                  )}
                </TableCell>
                <TableCell className="text-xs text-destructive max-w-md truncate">
                  {error}
                </TableCell>
              </TableRow>
            );
          })}
          {events.length === 0 && (
            <TableRow>
              <TableCell colSpan={6} className="text-center text-xs text-muted-foreground">
                (没有 propose 记录)
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}

// ============================================================================
// Curator tab —— 4-bucket 静态建议
// ============================================================================

function CuratorPanel() {
  const [buckets, setBuckets] = useState<SkillCurateBuckets | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setLoadErr(null);
    try {
      const res = await api.curateSkills();
      setBuckets(res);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const totalCount = useMemo(() => {
    if (!buckets) return 0;
    return (
      buckets.stale.length +
      buckets.underused.length +
      buckets.failing.length +
      buckets.overlapping.length
    );
  }, [buckets]);

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <p className="text-sm text-muted-foreground">
          read-only 静态分类建议(stale / underused / failing / overlapping)。建议不自动改 skill 状态,需手工决定。
          {buckets && <span className="ml-2">— total {totalCount}</span>}
        </p>
        <Button size="sm" variant="outline" disabled={busy} onClick={load}>
          Re-run
        </Button>
      </div>
      {loadErr && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {loadErr}
        </div>
      )}

      {buckets && (
        <div className="grid gap-4 md:grid-cols-2">
          <BucketCard
            title="Stale"
            description="近 30 天没被 activate"
            items={buckets.stale}
          />
          <BucketCard
            title="Underused"
            description="总 activate 次数 < 3"
            items={buckets.underused}
          />
          <BucketCard
            title="Failing"
            description="最近 10 次里 error 比例 > 50%"
            items={buckets.failing}
          />
          <OverlapCard items={buckets.overlapping} />
        </div>
      )}
    </div>
  );
}

function BucketCard({
  title,
  description,
  items,
}: {
  title: string;
  description: string;
  items: string[];
}) {
  return (
    <div className="rounded-lg border border-border p-4 bg-card/40 space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{title}</h3>
        <Badge variant="outline">{items.length}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">{description}</p>
      <div className="flex flex-wrap gap-1">
        {items.length === 0 ? (
          <span className="text-xs text-muted-foreground">(空)</span>
        ) : (
          items.map((name) => (
            <Badge key={name} variant="secondary" className="font-mono text-xs">
              {name}
            </Badge>
          ))
        )}
      </div>
    </div>
  );
}

function OverlapCard({ items }: { items: { a: string; b: string; ratio: number }[] }) {
  return (
    <div className="rounded-lg border border-border p-4 bg-card/40 space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Overlapping</h3>
        <Badge variant="outline">{items.length}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">prompt SequenceMatcher.ratio() &gt; 0.75</p>
      <div className="space-y-1">
        {items.length === 0 ? (
          <span className="text-xs text-muted-foreground">(空)</span>
        ) : (
          items.map((pair, i) => (
            <div
              key={`${pair.a}-${pair.b}-${i}`}
              className="flex justify-between text-xs font-mono border-b border-border/40 py-1"
            >
              <span>
                {pair.a} ↔ {pair.b}
              </span>
              <span className="text-muted-foreground">ratio={pair.ratio}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
