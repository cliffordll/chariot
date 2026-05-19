import { ChevronDown } from "lucide-react";
import { type ReactNode, useState } from "react";

export function CollapsibleJson({
  title,
  value,
  defaultExpanded: _defaultExpanded = false,
  maxHeightClassName = "max-h-56",
  containerClassName = "rounded-lg border border-border bg-muted/10 p-4",
  contentClassName = "rounded bg-background/80 p-3 font-mono text-[11px] leading-5",
}: {
  title?: string;
  value: unknown;
  defaultExpanded?: boolean;
  maxHeightClassName?: string;
  containerClassName?: string;
  contentClassName?: string;
}) {
  return (
    <div className={`space-y-2 ${containerClassName}`}>
      {title ? <div className="text-sm font-medium">{title}</div> : null}
      <div className={`${maxHeightClassName} overflow-auto ${contentClassName}`}>
        <JsonTree value={value} />
      </div>
    </div>
  );
}

export function JsonTree({ value }: { value: unknown }) {
  return <JsonNode value={value} depth={0} />;
}

function JsonNode({
  value,
  depth,
  label,
  trailingComma = false,
}: {
  value: unknown;
  depth: number;
  label?: string;
  trailingComma?: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  const indent = { paddingLeft: `${depth * 16}px` };

  if (value === null) {
    return (
      <ScalarRow label={label} value={<span className="text-muted-foreground">null</span>} depth={depth} trailingComma={trailingComma} />
    );
  }

  if (typeof value === "string") {
    return (
      <ScalarRow
        label={label}
        value={<span className="break-all text-emerald-700">"{value}"</span>}
        depth={depth}
        trailingComma={trailingComma}
      />
    );
  }

  if (typeof value === "number" || typeof value === "boolean") {
    return (
      <ScalarRow
        label={label}
        value={<span className="text-violet-700">{String(value)}</span>}
        depth={depth}
        trailingComma={trailingComma}
      />
    );
  }

  if (Array.isArray(value)) {
    const suffix = `Array(${value.length})`;
    return (
      <div>
        <button
          type="button"
          className="flex items-center gap-1 text-left hover:text-foreground"
          style={indent}
          onClick={() => setExpanded((cur) => !cur)}
        >
          <ChevronDown className={"h-3 w-3 text-muted-foreground transition " + (expanded ? "" : "-rotate-90")} />
          {label ? <span className="text-sky-700">"{label}"</span> : null}
          {label ? <span>: </span> : null}
          <span>[</span>
          {!expanded ? <span className="text-muted-foreground"> {suffix} ]</span> : null}
          {trailingComma && !expanded ? "," : ""}
        </button>
        {expanded && (
          <>
            {value.map((item, index) => (
              <JsonNode
                key={index}
                value={item}
                depth={depth + 1}
                trailingComma={index < value.length - 1}
              />
            ))}
          <div style={indent} className="leading-5">
            <span>]</span>
            {trailingComma ? "," : ""}
          </div>
          </>
        )}
      </div>
    );
  }

  const entries = Object.entries(value as Record<string, unknown>);
  const suffix = `Object(${entries.length})`;

  return (
    <div>
      <button
        type="button"
        className="flex items-center gap-1 text-left hover:text-foreground"
        style={indent}
        onClick={() => setExpanded((cur) => !cur)}
      >
        <ChevronDown className={"h-3 w-3 text-muted-foreground transition " + (expanded ? "" : "-rotate-90")} />
        {label ? <span className="text-sky-700">"{label}"</span> : null}
        {label ? <span>: </span> : null}
        <span>{"{"}</span>
        {!expanded ? <span className="text-muted-foreground"> {suffix} {"}"}</span> : null}
        {trailingComma && !expanded ? "," : ""}
      </button>
      {expanded && (
        <>
          {entries.map(([key, item], index) => (
            <JsonNode
              key={key}
              value={item}
              depth={depth + 1}
              label={key}
              trailingComma={index < entries.length - 1}
            />
          ))}
          <div style={indent} className="leading-5">
            <span>{"}"}</span>
            {trailingComma ? "," : ""}
          </div>
        </>
      )}
    </div>
  );
}

function ScalarRow({
  label,
  value,
  depth,
  trailingComma,
}: {
  label?: string;
  value: ReactNode;
  depth: number;
  trailingComma: boolean;
}) {
  const indent = { paddingLeft: `${depth * 16}px` };

  if (!label) {
    return (
      <div style={indent} className="leading-5">
        {value}
        {trailingComma ? "," : ""}
      </div>
    );
  }

  return (
    <div style={indent} className="grid grid-cols-[max-content_1fr] gap-x-2 leading-5">
      <span className="text-sky-700">"{label}":</span>
      <span>
        {value}
        {trailingComma ? "," : ""}
      </span>
    </div>
  );
}
