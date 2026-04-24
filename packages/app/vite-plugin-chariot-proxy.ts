/**
 * chariot-proxy:per-request 动态代理 /admin + /v1 到本地 chariot server。
 *
 * 为什么不用 `server.proxy`:vite 的 http-proxy 在启动时把 target 固化,server 重启换
 * 端口后(endpoint.json 刷新)vite 代理还指旧端口,全部 502。
 *
 * 本插件在 vite 的 connect middleware 栈里拦截 `/admin/*` 和 `/v1/*`,每次请求时现读
 * `~/.chariot/endpoint.json`(或 `VITE_API_URL`)拿当前上游 URL,用 node http 直接
 * 透传;SSE 靠 pipe 自然流式,不缓冲。
 *
 * 环境变量 `VITE_API_URL` 优先级 > `endpoint.json`。
 */

import { existsSync, readFileSync } from "node:fs";
import http, { type IncomingMessage, type ServerResponse } from "node:http";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import { URL } from "node:url";

import type { Plugin, ViteDevServer } from "vite";

const ENDPOINT_PATH = path.join(os.homedir(), ".chariot", "endpoint.json");
const PROXY_PREFIXES = ["/admin", "/v1"] as const;

function resolveTarget(): string | null {
  const fromEnv = process.env.VITE_API_URL;
  if (fromEnv && fromEnv.trim()) return fromEnv.trim();
  if (!existsSync(ENDPOINT_PATH)) return null;
  try {
    const raw: unknown = JSON.parse(readFileSync(ENDPOINT_PATH, "utf-8"));
    if (raw && typeof raw === "object" && "url" in raw) {
      const u = (raw as { url: unknown }).url;
      return typeof u === "string" ? u : null;
    }
  } catch {
    // fallthrough
  }
  return null;
}

function matchPrefix(url: string | undefined): boolean {
  if (!url) return false;
  return PROXY_PREFIXES.some((p) => url === p || url.startsWith(`${p}/`) || url.startsWith(`${p}?`));
}

function sendError(res: ServerResponse, status: number, body: Record<string, unknown>): void {
  if (res.headersSent) {
    res.end();
    return;
  }
  res.statusCode = status;
  res.setHeader("content-type", "application/json; charset=utf-8");
  res.end(JSON.stringify(body));
}

export function chariotProxy(): Plugin {
  let lastLoggedTarget: string | null = null;

  return {
    name: "chariot-proxy",
    configureServer(server: ViteDevServer) {
      server.middlewares.use((req: IncomingMessage, res: ServerResponse, next) => {
        const url = req.url ?? "";
        if (!matchPrefix(url)) return next();

        const target = resolveTarget();
        if (!target) {
          sendError(res, 502, {
            error: "chariot server unreachable",
            detail: "~/.chariot/endpoint.json 不存在,且未设 VITE_API_URL",
          });
          return;
        }

        if (target !== lastLoggedTarget) {
          server.config.logger.info(`[chariot-proxy] → ${target}`);
          lastLoggedTarget = target;
        }

        let targetUrl: URL;
        try {
          targetUrl = new URL(target);
        } catch {
          sendError(res, 502, { error: "invalid target url", target });
          return;
        }

        const transport = targetUrl.protocol === "https:" ? https : http;
        const upstream = transport.request(
          {
            protocol: targetUrl.protocol,
            hostname: targetUrl.hostname,
            port: targetUrl.port || (targetUrl.protocol === "https:" ? 443 : 80),
            method: req.method,
            path: url,
            headers: { ...req.headers, host: targetUrl.host },
          },
          (upRes: IncomingMessage) => {
            res.writeHead(upRes.statusCode ?? 502, upRes.headers);
            upRes.pipe(res);
          },
        );

        upstream.on("error", (err: NodeJS.ErrnoException) => {
          const stale =
            err.code === "ECONNREFUSED" ||
            err.code === "ECONNRESET" ||
            err.code === "EHOSTUNREACH";
          const hint = stale
            ? "上游 server 连不上(可能是 endpoint.json 陈旧,server 已挂)。请跑 `chariot start` 或 `python -m chariot.server` 重启。"
            : "转发到上游失败";
          server.config.logger.warn(
            `[chariot-proxy] upstream error for ${req.method} ${url}: ${err.code ?? ""} ${err.message}`,
            { timestamp: true },
          );
          sendError(res, 502, {
            error: "chariot proxy failed",
            code: err.code,
            detail: err.message,
            target,
            hint,
          });
        });

        req.on("aborted", () => {
          upstream.destroy();
        });

        req.pipe(upstream);
      });
    },
  };
}
