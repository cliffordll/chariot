import { NavLink } from "react-router";

import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "@/routes";

export function Nav() {
  return (
    <aside className="w-56 shrink-0 border-r border-border bg-card/50 px-4 py-6">
      <div className="mb-6 px-2 text-lg font-semibold tracking-tight">Chariot</div>
      <nav className="flex flex-col gap-1">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              cn(
                "rounded-md px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-accent-foreground",
              )
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
