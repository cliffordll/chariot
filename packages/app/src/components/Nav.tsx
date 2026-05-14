import { useState } from "react";
import { NavLink } from "react-router";

import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "@/routes";

const SECTIONS = [
  { title: null, items: [NAV_ITEMS[0], NAV_ITEMS[1]] },
  { title: "Config", items: [NAV_ITEMS[3], NAV_ITEMS[4], NAV_ITEMS[5], NAV_ITEMS[6], NAV_ITEMS[7], NAV_ITEMS[8]] },
  { title: "Agents", items: [NAV_ITEMS[9], NAV_ITEMS[10]] },
  { title: "Ops", items: [NAV_ITEMS[11], NAV_ITEMS[12], NAV_ITEMS[2], NAV_ITEMS[14], NAV_ITEMS[15], NAV_ITEMS[16], NAV_ITEMS[13]] },
] as const;

export function Nav() {
  const [openMap, setOpenMap] = useState(() =>
    SECTIONS.map((s) => s.title !== null),
  );

  const toggle = (si: number) => {
    setOpenMap((prev) => {
      const next = [...prev];
      next[si] = !next[si];
      return next;
    });
  };

  return (
    <aside className="w-56 shrink-0 border-r border-border bg-card/50 px-4 py-4">
      <div className="mb-3 px-2 text-lg font-semibold tracking-tight">Chariot</div>
      <nav className="flex flex-col gap-0.5">
        {SECTIONS.map((section, si) => {
          const isOpen = openMap[si];
          const collapsible = section.title !== null;

          return (
            <div key={si}>
              {collapsible ? (
                <button
                  type="button"
                  onClick={() => toggle(si)}
                  className="mt-3 mb-1 flex w-full items-center justify-between rounded-md bg-accent/40 px-3 py-1.5 text-xs font-bold uppercase tracking-wider text-foreground transition-colors hover:bg-accent/60"
                >
                  <span>{section.title}</span>
                  <span className="select-none text-muted-foreground">{isOpen ? "▾" : "▸"}</span>
                </button>
              ) : null}
              {(!collapsible || isOpen) && (
                <div className="flex flex-col gap-0.5">
                  {section.items.map((item) => (
                    <NavLink
                      key={item.path}
                      to={item.path}
                      className={({ isActive }) =>
                        cn(
                          "block rounded-md px-3 py-1.5 text-sm transition-colors",
                          isActive
                            ? "bg-accent text-accent-foreground"
                            : "text-muted-foreground hover:bg-accent/60 hover:text-accent-foreground",
                        )
                      }
                    >
                      {item.label}
                    </NavLink>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
