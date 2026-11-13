#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use std::path::Path;
use std::sync::{Mutex, MutexGuard};
use std::thread;
use std::time::{Duration, Instant};

use backend::{BackendConnection, BackendProcess};
use tauri::{AppHandle, Manager, RunEvent, State};

#[derive(Default)]
enum BackendSlot {
    #[default]
    Starting,
    Ready(BackendProcess),
    Failed(String),
    Stopped,
}

#[derive(Default)]
struct BackendState(Mutex<BackendSlot>);

impl BackendState {
    fn lock(&self) -> MutexGuard<'_, BackendSlot> {
        self.0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

/// The only command exposed to the webview: where the local API is and its per-launch token.
#[tauri::command]
fn backend_connection(state: State<'_, BackendState>) -> Result<BackendConnection, String> {
    match &*state.lock() {
        BackendSlot::Ready(process) => Ok(process.connection()),
        BackendSlot::Starting => Err("BACKEND_STARTING".into()),
        BackendSlot::Failed(reason) => Err(format!("BACKEND_FAILED: {reason}")),
        BackendSlot::Stopped => Err("BACKEND_FAILED: stopped".into()),
    }
}

const MAX_RESTARTS: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(60);
const WATCH_INTERVAL: Duration = Duration::from_millis(500);

/// Starts the local API and keeps it alive: an unexpected exit (crash, kill) triggers a
/// restart with a fresh port and token, at most `MAX_RESTARTS` times per `RESTART_WINDOW`.
/// The UI re-reads `backend_connection` when a request cannot reach the API.
fn supervise(handle: &AppHandle, data_dir: &Path) {
    let mut restarts: Vec<Instant> = Vec::new();
    loop {
        let result = backend::spawn(data_dir);
        {
            let state = handle.state::<BackendState>();
            let mut slot = state.lock();
            if matches!(*slot, BackendSlot::Stopped) {
                if let Ok(process) = result {
                    process.shutdown();
                }
                return;
            }
            match result {
                Ok(process) => *slot = BackendSlot::Ready(process),
                Err(error) => {
                    eprintln!("[pricepilot] local API failed to start: {error}");
                    *slot = BackendSlot::Failed(error.to_string());
                    return;
                }
            }
        }
        loop {
            thread::sleep(WATCH_INTERVAL);
            let state = handle.state::<BackendState>();
            let mut slot = state.lock();
            let exited = match &mut *slot {
                BackendSlot::Ready(process) => process.has_exited(),
                _ => return, // stopped by the app or failed: nothing to supervise
            };
            if exited {
                *slot = BackendSlot::Starting;
                break;
            }
        }
        let now = Instant::now();
        restarts.retain(|t| now.duration_since(*t) < RESTART_WINDOW);
        if restarts.len() >= MAX_RESTARTS {
            let reason = "локальный сервис несколько раз аварийно завершился".to_string();
            eprintln!("[pricepilot] {reason}; giving up");
            *handle.state::<BackendState>().lock() = BackendSlot::Failed(reason);
            return;
        }
        restarts.push(now);
        eprintln!("[pricepilot] local API exited unexpectedly; restarting");
    }
}

fn main() {
    let app = tauri::Builder::default()
        .manage(BackendState::default())
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            let handle = app.handle().clone();
            // Start and supervise the API off the main thread; the UI polls `backend_connection`.
            thread::spawn(move || supervise(&handle, &data_dir));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_connection])
        .build(tauri::generate_context!())
        .expect("failed to build the PricePilot application");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let previous = std::mem::replace(
                &mut *handle.state::<BackendState>().lock(),
                BackendSlot::Stopped,
            );
            if let BackendSlot::Ready(process) = previous {
                process.shutdown();
            }
        }
    });
}
