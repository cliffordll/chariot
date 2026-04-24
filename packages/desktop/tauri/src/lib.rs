//! Tauri 桌面外壳主逻辑。
//!
//! 职责
//! ----
//! - `setup` 钩子:
//!   1. spawn `chariot-server` sidecar(`--parent-pid` 传当前 Tauri 进程 PID;
//!      server 的 watcher 在 Tauri 挂了 5s 内会自动 graceful_shutdown,DESIGN §6)
//!   2. 建系统托盘(图标复用窗口 icon;右键菜单 Show / Exit;左键点图标显示窗口)
//! - `tauri-plugin-window-state`:自动记忆窗口位置 / 大小,重开时恢复
//! - 关窗拦截:点 X 按钮 → 隐到托盘(不真退出),符合桌面 app 一贯体验
//! - Exit 菜单项:主动发一次 `POST /admin/shutdown` 让 server 起 graceful_shutdown,
//!   然后 `app.exit(0)`;即使这一步失败,`--parent-pid` watcher 也会在 5s 内兜底
//! - `get_server_url` command:读 `~/.chariot/endpoint.json` 的 `url` 字段;prod
//!   模式下 webview 没 vite proxy 时前端 invoke 拿 base URL(7.x 前端再适配)

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, State, WindowEvent};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

/// sidecar 进程句柄;Drop 时不杀 child(依赖 server 的 parent-pid watcher 兜底)
struct SidecarState(Mutex<Option<CommandChild>>);

#[derive(Deserialize)]
struct Endpoint {
    url: String,
}

fn read_endpoint_url() -> Option<String> {
    let home = dirs::home_dir()?;
    let raw = std::fs::read_to_string(home.join(".chariot").join("endpoint.json")).ok()?;
    let ep: Endpoint = serde_json::from_str(&raw).ok()?;
    Some(ep.url)
}

#[tauri::command]
fn get_server_url() -> Result<String, String> {
    read_endpoint_url().ok_or_else(|| "无法读取 ~/.chariot/endpoint.json(server 可能未启动)".into())
}

/// 前端 invoke 的 "Check for updates" 入口。
///
/// 成功路径:返回 `UpdateCheckResult { available, version?, notes? }`。
/// 无新版本 → `available: false`。发现新版本但不自动下载,由前端展示信息,
/// 用户点"立即更新"再走 `install_update` 命令(下载 + 应用 + 重启)。
#[tauri::command]
async fn check_for_update(app: AppHandle) -> Result<UpdateCheckResult, String> {
    let updater = app.updater().map_err(|e| format!("updater 初始化失败:{e}"))?;
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
    let updater = app.updater().map_err(|e| format!("updater 初始化失败:{e}"))?;
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

/// 用 std::net 直接发 HTTP POST,避免拉 ureq / reqwest 进 bundle。
/// 2s 超时;response body 读到 EOF 或超时为止(不管状态码,尽力而为)。
fn post_shutdown() {
    let Some(url) = read_endpoint_url() else {
        return;
    };
    let Some(host_port) = url
        .strip_prefix("http://")
        .map(|s| s.trim_end_matches('/'))
    else {
        return;
    };

    let Ok(mut stream) = TcpStream::connect(host_port) else {
        return;
    };
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));

    let req = format!(
        "POST /admin/shutdown HTTP/1.1\r\n\
         Host: {host_port}\r\n\
         Content-Length: 0\r\n\
         Connection: close\r\n\
         \r\n"
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return;
    }
    let mut sink = Vec::with_capacity(512);
    let _ = stream.read_to_end(&mut sink);
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
    // 主动触发 server graceful_shutdown(最多阻塞 2s;失败靠 parent-pid watcher 兜底)
    post_shutdown();
    app.exit(0);
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
        .manage(SidecarState(Mutex::new(None)))
        .setup(|app| {
            // --- sidecar ---
            let parent_pid = std::process::id().to_string();
            let (_rx, child) = app
                .shell()
                .sidecar("chariot-server")
                .map_err(|e| format!("找不到 chariot-server sidecar:{e}"))?
                .args(["--parent-pid", &parent_pid])
                .spawn()
                .map_err(|e| format!("spawn chariot-server 失败:{e}"))?;

            let state: State<SidecarState> = app.state();
            if let Ok(mut guard) = state.0.lock() {
                *guard = Some(child);
            }

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
            get_server_url,
            check_for_update,
            install_update,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
