#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use std::sync::{Mutex, MutexGuard};

use backend::{BackendConnection, BackendProcess};
use tauri::{Manager, RunEvent, State};

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

fn main() {
    let app = tauri::Builder::default()
        .manage(BackendState::default())
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            let handle = app.handle().clone();
            // Start the API off the main thread; the UI polls `backend_connection`.
            std::thread::spawn(move || {
                let result = backend::spawn(&data_dir);
                let state = handle.state::<BackendState>();
                let mut slot = state.lock();
                if matches!(*slot, BackendSlot::Stopped) {
                    if let Ok(process) = result {
                        process.shutdown();
                    }
                    return;
                }
                *slot = match result {
                    Ok(process) => BackendSlot::Ready(process),
                    Err(error) => {
                        eprintln!("[pricepilot] local API failed to start: {error}");
                        BackendSlot::Failed(error.to_string())
                    }
                };
            });
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
