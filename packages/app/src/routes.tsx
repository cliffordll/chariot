import { Navigate, Route, Routes } from "react-router";

import Chat from "@/pages/Chat";
import Dashboard from "@/pages/Dashboard";
import Logs from "@/pages/Logs";
import Models from "@/pages/Models";
import Tools from "@/pages/Tools";

export const NAV_ITEMS = [
  { path: "/dashboard", label: "Dashboard" },
  { path: "/models", label: "Models" },
  { path: "/tools", label: "Tools" },
  { path: "/logs", label: "Logs" },
  { path: "/chat", label: "Chat" },
] as const;

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/models" element={<Models />} />
      <Route path="/tools" element={<Tools />} />
      <Route path="/logs" element={<Logs />} />
      <Route path="/chat" element={<Chat />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
