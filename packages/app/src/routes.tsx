import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";

const Dashboard = lazy(() => import("@/pages/Dashboard"));
const Agents = lazy(() => import("@/pages/Agents"));
const Tasks = lazy(() => import("@/pages/Tasks"));
const Jobs = lazy(() => import("@/pages/Jobs"));
const Providers = lazy(() => import("@/pages/Providers"));
const Tools = lazy(() => import("@/pages/Tools"));
const Toolsets = lazy(() => import("@/pages/Toolsets"));
const Memory = lazy(() => import("@/pages/Memory"));
const Prompt = lazy(() => import("@/pages/Prompt"));
const Context = lazy(() => import("@/pages/Context"));
const Logs = lazy(() => import("@/pages/Logs"));
const Traces = lazy(() => import("@/pages/Traces"));
const Evals = lazy(() => import("@/pages/Evals"));
const Chat = lazy(() => import("@/pages/Chat"));
const Conversations = lazy(() => import("@/pages/Conversations"));
const Security = lazy(() => import("@/pages/Security"));
const Skills = lazy(() => import("@/pages/Skills"));

export const NAV_ITEMS = [
  { path: "/dashboard", label: "Dashboard" },
  { path: "/chat", label: "Chat" },
  { path: "/conversations", label: "Conversations" },
  { path: "/providers", label: "Providers" },
  { path: "/tools", label: "Tools" },
  { path: "/toolsets", label: "Toolsets" },
  { path: "/memory", label: "Memory" },
  { path: "/prompt", label: "Prompt" },
  { path: "/context", label: "Context" },
  { path: "/agents", label: "Agents" },
  { path: "/skills", label: "Skills" },
  { path: "/tasks", label: "Tasks" },
  { path: "/jobs", label: "Jobs" },
  { path: "/logs", label: "Logs" },
  { path: "/traces", label: "Traces" },
  { path: "/evals", label: "Evals" },
  { path: "/security", label: "Security" },
] as const;

export function AppRoutes() {
  return (
    <Suspense fallback={<RouteSkeleton />}>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/providers" element={<Providers />} />
        <Route path="/tools" element={<Tools />} />
        <Route path="/toolsets" element={<Toolsets />} />
        <Route path="/memory" element={<Memory />} />
        <Route path="/prompt" element={<Prompt />} />
        <Route path="/context" element={<Context />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/traces" element={<Traces />} />
        <Route path="/evals" element={<Evals />} />
        <Route path="/security" element={<Security />} />
        <Route path="/skills" element={<Skills />} />
        <Route path="/conversations" element={<Conversations />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Suspense>
  );
}

function RouteSkeleton() {
  return (
    <div className="space-y-4">
      <div className="h-10 w-48 animate-pulse rounded-full bg-muted/70" />
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="h-64 animate-pulse rounded-3xl border border-border bg-card/60" />
        <div className="h-64 animate-pulse rounded-3xl border border-border bg-card/60" />
      </div>
    </div>
  );
}
