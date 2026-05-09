import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 0.6.5 起前端走 Tauri.invoke / event.listen,不再有 HTTP 转发。Vite 仅用于
// 给 Tauri devURL 或纯 UI 调试服务静态资源 + HMR。
export default defineConfig(() => ({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    strictPort: false,
  },
}));
