import { Navigate, Route, Routes } from "react-router";

import Chat from "@/pages/Chat";
import Context from "@/pages/Context";
import Dashboard from "@/pages/Dashboard";
import Logs from "@/pages/Logs";
import Prompt from "@/pages/Prompt";
import Providers from "@/pages/Providers";
import Tools from "@/pages/Tools";

export const NAV_ITEMS = [
  { path: "/dashboard", label: "Dashboard" },
  { path: "/providers", label: "Providers" },
  { path: "/tools", label: "Tools" },
  { path: "/prompt", label: "Prompt" },
  { path: "/context", label: "Context" },
  { path: "/logs", label: "Logs" },
  { path: "/chat", label: "Chat" },
] as const;

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/providers" element={<Providers />} />
      <Route path="/tools" element={<Tools />} />
      <Route path="/prompt" element={<Prompt />} />
      <Route path="/context" element={<Context />} />
      <Route path="/logs" element={<Logs />} />
      <Route path="/chat" element={<Chat />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
