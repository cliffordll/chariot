import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, type LogOut } from "@/lib/api";

const PAGE_SIZE = 50;

export default function Logs() {
  const [items, setItems] = useState<LogOut[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  const load = useCallback(async (opts: { offset: number }) => {
    setLoadErr(null);
    try {
      const list = await api.listLogs({
        limit: PAGE_SIZE,
        offset: opts.offset,
      });
      setItems(list);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e));
      setItems([]);
    }
  }, []);

  useEffect(() => {
    void load({ offset });
  }, [load, offset]);

  function refresh() {
    void load({ offset });
  }

  const canPrev = offset > 0;
  const canNext = items !== null && items.length === PAGE_SIZE;

  return (
    <section>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Logs</h1>
        <Button variant="outline" onClick={refresh}>
          Refresh
        </Button>
      </div>

      {loadErr && (
        <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          {loadErr}
        </div>
      )}

      {items === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : items.length === 0 && !loadErr ? (
        <EmptyState />
      ) : (
        <div className="rounded-lg border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-44">created_at</TableHead>
                <TableHead>model</TableHead>
                <TableHead className="w-24">status</TableHead>
                <TableHead className="w-20 text-right">latency</TableHead>
                <TableHead className="w-24 text-right">in→out</TableHead>
                <TableHead>error</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((entry) => (
                <TableRow key={entry.id}>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {formatDate(entry.created_at)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {entry.model ?? "-"}
                  </TableCell>
                  <TableCell>{statusBadge(entry.status)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {entry.latency_ms !== null ? `${entry.latency_ms}ms` : "-"}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {(entry.input_tokens ?? 0)}→{(entry.output_tokens ?? 0)}
                  </TableCell>
                  <TableCell className="max-w-xs truncate text-xs text-muted-foreground">
                    {entry.error ?? ""}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
        <div>
          offset {offset}
          {items !== null ? ` · ${items.length} rows` : ""}
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!canPrev}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            Prev
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!canNext}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </Button>
        </div>
      </div>
    </section>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-border p-10 text-center">
      <p className="text-sm text-muted-foreground">
        暂无日志。发一条 chat 请求后点 Refresh 查看。
      </p>
    </div>
  );
}

function statusBadge(s: string) {
  if (s === "ok") return <Badge>ok</Badge>;
  if (s === "error") return <Badge variant="destructive">error</Badge>;
  return <Badge variant="outline">{s}</Badge>;
}

function formatDate(iso: string): string {
  // server 存 UTC;UI 展示本地时间
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}
