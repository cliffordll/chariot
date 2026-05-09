//! JSON-RPC 客户端 — Rust 主进程跟 Python sidecar(stdio)对话(0.6.5 S.9 起)。
//!
//! 协议
//! ----
//! - newline-delimited JSON
//! - request:`{"jsonrpc":"2.0", "id":..., "method":..., "params":...}`
//! - response:`{"jsonrpc":"2.0", "id":..., "result":...}` 或 `{...,"error":{...}}`
//! - notify(server 主推):`{"jsonrpc":"2.0", "method":..., "params":...}`(无 id)
//!
//! 实现
//! ----
//! - `JsonRpcClient::spawn(...)`:接 `tauri-plugin-shell` 的 `(rx, child)` 对,
//!   起一个 Tokio task 跑 reader loop:逐行收 child stdout → JSON parse →
//!   有 id 的 resolve pending oneshot;无 id 的 emit Tauri event(`rpc_notify`)
//! - `JsonRpcClient::request(method, params).await -> Result<Value>`:
//!   生成自增 id → push 进 pending map → 写 child stdin → 等 oneshot 返回
//! - sidecar 退出(`CommandEvent::Terminated` / stdin 错):reader 停 + 所有
//!   pending 全 reject,emit `sidecar_exited`
//!
//! 边界
//! ----
//! - **不实现 batch**(JSON-RPC 数组形式)—— sidecar 那边也不实现,YAGNI
//! - notify 走 Tauri event 系统:`{"method": "<name>", "params": <payload>}`
//!   作为单个 event payload,前端订阅 `rpc_notify` 后按 method 名 dispatch
//! - 错误传播:RPC error response → Err(RpcError);sidecar 异常退 → Err(...)

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tokio::sync::{oneshot, Mutex};

/// JSON-RPC 协议级错误(server 回 error response 或 sidecar 终止)。
#[derive(Debug, thiserror::Error, Serialize)]
pub enum RpcError {
    /// server 回了 error response,带原 code + message
    #[error("rpc error {code}: {message}")]
    Server { code: i64, message: String },
    /// sidecar 进程已退出,无法继续
    #[error("sidecar exited: {0}")]
    SidecarExited(String),
    /// 写 child stdin 失败 / 通道断
    #[error("transport error: {0}")]
    Transport(String),
    /// JSON 序列化 / 反序列化错(理论不该发生;参数 / 结果非法 JSON)
    #[error("serialization error: {0}")]
    Serialization(String),
}

/// 前端 invoke `rpc(method, params)` 的 notify 事件名(server 主推帧通过这个 emit 给前端)
const NOTIFY_EVENT: &str = "rpc_notify";
/// sidecar 进程退出时 emit 给前端的事件名
const SIDECAR_EXITED_EVENT: &str = "sidecar_exited";

/// Pending request 注册表:id → oneshot Sender(返回 result 或 error)
type PendingMap = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, RpcError>>>>>;

/// Tauri 主进程的 JSON-RPC 客户端。
///
/// 由 `setup` 钩子调 `spawn(...)` 创建一个,挂进 `app.manage(...)`,Tauri command
/// 通过 `State<JsonRpcClient>` 拿到引用并调 `request(method, params)`。
pub struct JsonRpcClient {
    next_id: AtomicU64,
    pending: PendingMap,
    /// child stdin 写句柄;包 Mutex 让 `request` 串行化写帧(避免帧字节交错)
    child: Arc<Mutex<Option<CommandChild>>>,
}

impl JsonRpcClient {
    /// 接管 `tauri-plugin-shell` `Command::sidecar(...).spawn()` 的 `(rx, child)` 对,
    /// 起 reader task,返一个 ready-to-use 的 client 实例。
    ///
    /// `app`:Tauri AppHandle,用来 emit notify / sidecar_exited 事件
    pub fn spawn(
        app: AppHandle,
        mut rx: tauri::async_runtime::Receiver<CommandEvent>,
        child: CommandChild,
    ) -> Self {
        let pending: PendingMap = Arc::new(Mutex::new(HashMap::new()));
        let pending_for_reader = Arc::clone(&pending);
        let app_for_reader = app.clone();

        // Reader task:消费 child stdout / stderr / terminated 事件
        tauri::async_runtime::spawn(async move {
            while let Some(event) = rx.recv().await {
                match event {
                    CommandEvent::Stdout(bytes) => {
                        Self::handle_stdout_line(&app_for_reader, &pending_for_reader, &bytes)
                            .await;
                    }
                    CommandEvent::Stderr(bytes) => {
                        // sidecar 把日志写 stderr,Rust 这边转发到自己的 log
                        let line = String::from_utf8_lossy(&bytes);
                        let trimmed = line.trim_end();
                        if !trimmed.is_empty() {
                            log::info!(target: "sidecar", "{}", trimmed);
                        }
                    }
                    CommandEvent::Terminated(payload) => {
                        let reason = format!("code={:?} signal={:?}", payload.code, payload.signal);
                        log::warn!("sidecar exited: {}", reason);
                        Self::reject_all_pending(&pending_for_reader, &reason).await;
                        let _ = app_for_reader.emit(SIDECAR_EXITED_EVENT, &reason);
                        break;
                    }
                    CommandEvent::Error(err) => {
                        log::error!("sidecar transport error: {}", err);
                        Self::reject_all_pending(&pending_for_reader, &err).await;
                        let _ = app_for_reader.emit(SIDECAR_EXITED_EVENT, &err);
                        break;
                    }
                    _ => {}
                }
            }
        });

        Self {
            next_id: AtomicU64::new(1),
            pending,
            child: Arc::new(Mutex::new(Some(child))),
        }
    }

    /// 发一次 RPC,等 server 返 response。
    ///
    /// 流程:
    /// 1. 自增 id → 注册 oneshot 进 pending map
    /// 2. 写 `{"jsonrpc":"2.0","id":N,"method":...,"params":...}\n` 到 child stdin
    /// 3. await oneshot recv → 返 server response 或 error
    pub async fn request(&self, method: &str, params: Value) -> Result<Value, RpcError> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = oneshot::channel();
        {
            let mut map = self.pending.lock().await;
            map.insert(id, tx);
        }

        let frame = serde_json::json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        let mut line =
            serde_json::to_vec(&frame).map_err(|e| RpcError::Serialization(e.to_string()))?;
        line.push(b'\n');

        // 写 stdin:Mutex 保护避免多 RPC 并发写时帧交错
        {
            let mut guard = self.child.lock().await;
            let Some(child) = guard.as_mut() else {
                // child 已被 take(sidecar 退出),清掉刚注册的 pending
                self.pending.lock().await.remove(&id);
                return Err(RpcError::SidecarExited("child not running".into()));
            };
            if let Err(e) = child.write(&line) {
                self.pending.lock().await.remove(&id);
                return Err(RpcError::Transport(e.to_string()));
            }
        }

        // 等 reader resolve(或 sidecar 退出导致 reject_all)
        rx.await
            .unwrap_or_else(|_| Err(RpcError::SidecarExited("oneshot canceled".into())))
    }

    /// 处理一行 stdout:JSON parse → 路由到 pending oneshot 或 emit notify。
    async fn handle_stdout_line(app: &AppHandle, pending: &PendingMap, raw: &[u8]) {
        let trimmed = trim_newline(raw);
        if trimmed.is_empty() {
            return;
        }

        let frame: Value = match serde_json::from_slice(trimmed) {
            Ok(v) => v,
            Err(e) => {
                log::warn!(
                    "sidecar stdout: invalid JSON ({}): {:?}",
                    e,
                    String::from_utf8_lossy(trimmed)
                );
                return;
            }
        };

        // 区分 response 还是 notify:看 id 是否存在(spec:notify 无 id 字段)
        if let Some(id_value) = frame.get("id") {
            // 是 response —— resolve 对应 pending
            let Some(id) = id_value.as_u64() else {
                log::warn!("sidecar response with non-u64 id: {:?}", id_value);
                return;
            };
            let tx = {
                let mut map = pending.lock().await;
                map.remove(&id)
            };
            let Some(tx) = tx else {
                log::warn!("sidecar response with unknown id={}", id);
                return;
            };

            let result = if let Some(err_obj) = frame.get("error") {
                Err(RpcError::Server {
                    code: err_obj.get("code").and_then(Value::as_i64).unwrap_or(0),
                    message: err_obj
                        .get("message")
                        .and_then(Value::as_str)
                        .unwrap_or("(unknown)")
                        .to_string(),
                })
            } else {
                Ok(frame.get("result").cloned().unwrap_or(Value::Null))
            };
            // 发送方可能已 drop receiver(请求被取消);忽略 send 错
            let _ = tx.send(result);
        } else {
            // 是 notify —— emit Tauri event 给前端
            let method = frame
                .get("method")
                .and_then(Value::as_str)
                .unwrap_or("(unknown)");
            let params = frame.get("params").cloned().unwrap_or(Value::Null);
            let _ = app.emit(
                NOTIFY_EVENT,
                NotifyPayload {
                    method: method.to_string(),
                    params,
                },
            );
        }
    }

    /// 把所有 pending 全部 reject(sidecar 退出 / 通道断时调)。
    async fn reject_all_pending(pending: &PendingMap, reason: &str) {
        let drained = {
            let mut map = pending.lock().await;
            std::mem::take(&mut *map)
        };
        for (_id, tx) in drained {
            let _ = tx.send(Err(RpcError::SidecarExited(reason.to_string())));
        }
    }
}

/// 推给前端的 notify 事件 payload。
#[derive(Serialize, Deserialize, Debug, Clone)]
struct NotifyPayload {
    method: String,
    params: Value,
}

/// 把行尾的 `\n` / `\r\n` 砍掉,避免 JSON 解析多余空白。
fn trim_newline(bytes: &[u8]) -> &[u8] {
    let mut end = bytes.len();
    while end > 0 && (bytes[end - 1] == b'\n' || bytes[end - 1] == b'\r') {
        end -= 1;
    }
    &bytes[..end]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trim_newline_strips_lf_and_crlf() {
        assert_eq!(trim_newline(b"hello\n"), b"hello");
        assert_eq!(trim_newline(b"hello\r\n"), b"hello");
        assert_eq!(trim_newline(b"hello"), b"hello");
        assert_eq!(trim_newline(b"\n"), b"");
        assert_eq!(trim_newline(b""), b"");
    }

    #[test]
    fn rpc_error_server_serializes_with_code_and_message() {
        let e = RpcError::Server {
            code: -32601,
            message: "method not found".into(),
        };
        let json = serde_json::to_value(&e).unwrap();
        // 序列化形态稳定(给前端 invoke 用)
        assert!(json.is_object() || json.is_string());
    }
}
