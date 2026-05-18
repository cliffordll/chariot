//! Tauri 桌面外壳主逻辑(0.6.5 S.9 起 · stdio JSON-RPC sidecar)。
//!
//! 跟 0.5.0 的差异:
//! - 撤"spawn server.exe + 读 endpoint.json + httpx 调 HTTP"路径
//! - 改 spawn `chariot-sidecar.exe`(stdio JSON-RPC)+ `JsonRpcClient`
//!   维护 pending requests + reader/writer task
//! - Tauri command `rpc(method, params)` 暴露给前端,前端走 invoke
//! - notify 帧(server 主推 ChatEvent)走 Tauri event 系统:`rpc_notify`
//!
//! 职责
//! ----
//! - `setup` 钩子:
//!   1. spawn `chariot-sidecar` sidecar;接 (rx, child) 给 `JsonRpcClient::spawn`
//!   2. 建系统托盘(图标复用窗口 icon;右键菜单 Show / Exit;左键点图标显示窗口)
//! - `tauri-plugin-window-state`:自动记忆窗口位置 / 大小,重开时恢复
//! - 关窗拦截:点 X 按钮 → 隐到托盘(不真退出)
//! - Exit 菜单项:`app.exit(0)` 直接退;Drop child 时 child stdin 关 → sidecar
//!   收到 EOF → 自然退出(不需要主动 kill,对齐 CLAUDE.md sidecar 生命周期)
//! - `rpc(method, params)` command:走 `JsonRpcClient.request(...)`,返
//!   server response(或 RpcError)

mod rpc_client;

use serde::Serialize;
use serde_json::Value;
use std::path::PathBuf;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, State, WindowEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

use rpc_client::{JsonRpcClient, RpcError};

/// 前端 invoke 的 RPC 入口。`method` 是 sidecar 上注册的方法名(如 `list_convos`),
/// `params` 是任意 JSON 对象。返 server response.result,失败返 RpcError(序列化为
/// 前端可见的 error 对象)。
#[tauri::command]
async fn rpc(
    state: State<'_, JsonRpcClient>,
    method: String,
    params: Value,
) -> Result<Value, RpcError> {
    state.request(&method, params).await
}

/// 前端 invoke 的 "Check for updates" 入口。
///
/// 成功路径:返回 `UpdateCheckResult { available, version?, notes? }`。
/// 无新版本 → `available: false`。发现新版本但不自动下载,由前端展示信息,
/// 用户点"立即更新"再走 `install_update` 命令(下载 + 应用 + 重启)。
#[tauri::command]
async fn check_for_update(app: AppHandle) -> Result<UpdateCheckResult, String> {
    let updater = app
        .updater()
        .map_err(|e| format!("updater 初始化失败:{e}"))?;
    match updater.check().await {
        Ok(Some(update)) => Ok(UpdateCheckResult {
            available: true,
            version: Some(update.version.clone()),
            notes: update.body.clone(),
        }),
        Ok(None) => Ok(UpdateCheckResult {
            available: false,
            version: None,
            notes: None,
        }),
        Err(e) => Err(format!("check 失败:{e}")),
    }
}

/// 前端确认升级后调用:下载 + 应用 + 重启。
/// 阻塞直到下载完成,失败时把错误返前端让用户看到。
#[tauri::command]
async fn install_update(app: AppHandle) -> Result<(), String> {
    let updater = app
        .updater()
        .map_err(|e| format!("updater 初始化失败:{e}"))?;
    let update = updater
        .check()
        .await
        .map_err(|e| format!("check 失败:{e}"))?
        .ok_or_else(|| "当前已是最新版本,无需升级".to_string())?;

    update
        .download_and_install(|_chunk, _total| {}, || {})
        .await
        .map_err(|e| format!("下载/安装失败:{e}"))?;

    app.restart()
}

#[derive(Serialize)]
struct UpdateCheckResult {
    available: bool,
    version: Option<String>,
    notes: Option<String>,
}

fn show_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

fn request_exit(app: &AppHandle) {
    // 立即隐藏,用户感知即时关闭
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.hide();
    }
    // sidecar 通过 Drop child 时 stdin 关,sidecar 收到 EOF 自然退出
    // (CLAUDE.md sidecar 生命周期:不主动 kill)
    app.exit(0);
}

fn sidecar_workspace_dir() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    if let Some(workspace_root) = manifest_dir
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
    {
        return workspace_root.to_path_buf();
    }
    manifest_dir
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // single-instance 必须**最先**注册:第二个 Tauri 实例启动时,此 plugin 会
        // 拦截并把参数转交给主实例的回调,然后立即退出,从而避免双进程双 sidecar。
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main(app);
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            // --- spawn sidecar + 装载 JsonRpcClient ---
            let sidecar_cwd = sidecar_workspace_dir();
            let (rx, child) = app
                .shell()
                .sidecar("chariot-sidecar")
                .map_err(|e| format!("找不到 chariot-sidecar:{e}"))?
                .current_dir(sidecar_cwd)
                .spawn()
                .map_err(|e| format!("spawn chariot-sidecar 失败:{e}"))?;
            let client = JsonRpcClient::spawn(app.handle().clone(), rx, child);
            app.manage(client);

            // --- tray ---
            let show_item = MenuItem::with_id(app, "show", "Show", true, None::<&str>)?;
            let exit_item = MenuItem::with_id(app, "exit", "Exit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &exit_item])?;

            let _tray = TrayIconBuilder::with_id("main")
                .icon(
                    app.default_window_icon()
                        .cloned()
                        .ok_or("default_window_icon 不可用")?,
                )
                .tooltip("Chariot")
                .menu(&menu)
                // 左键点击托盘 → 显示窗口(不弹菜单);菜单只在右键
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main(app),
                    "exit" => request_exit(app),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // 关窗 X 按钮拦截 → 隐到托盘;真退出走托盘 Exit 菜单项
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .invoke_handler(tauri::generate_handler![
            rpc,
            check_for_update,
            install_update,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
