//! Lifecycle of the local FastAPI process.
//!
//! Protocol (see `backend/src/pricepilot/__main__.py`):
//! * the shell generates a random capability token and writes it as the first stdin line;
//! * the API binds `127.0.0.1:0` and prints `{"event":"ready","port":N}` on stdout;
//! * stdin stays open; closing it (shell exit or crash) makes the API shut down gracefully.
//!
//! The token never appears in arguments, environment, files or logs.

use std::fmt;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

const READY_TIMEOUT: Duration = Duration::from_secs(90);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);
const TAURI_ORIGINS: [&str; 3] = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
];
#[cfg(debug_assertions)]
const DEV_ORIGINS: [&str; 2] = ["http://127.0.0.1:5173", "http://localhost:5173"];

#[derive(Clone, Serialize)]
pub struct BackendConnection {
    pub base_url: String,
    pub token: String,
}

#[derive(Debug)]
pub enum BackendError {
    Random,
    DataDir,
    Spawn,
    Pipe,
    Refused(String),
    Exited,
    Timeout,
    NotPackaged,
}

impl fmt::Display for BackendError {
    // Messages are shown to the user: no paths, tokens or OS error details.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Random => write!(f, "не удалось сгенерировать токен"),
            Self::DataDir => write!(f, "нет доступа к каталогу данных приложения"),
            Self::Spawn => write!(
                f,
                "не удалось запустить локальный сервис (нужен uv в PATH для dev-сборки)"
            ),
            Self::Pipe => write!(f, "ошибка связи с локальным сервисом"),
            Self::Refused(code) => write!(f, "локальный сервис отказался запускаться ({code})"),
            Self::Exited => write!(f, "локальный сервис завершился при запуске"),
            Self::Timeout => write!(f, "локальный сервис не ответил вовремя"),
            Self::NotPackaged => write!(f, "локальный сервис не найден в пакете приложения"),
        }
    }
}

impl std::error::Error for BackendError {}

#[derive(Deserialize)]
struct ProcessEvent {
    event: String,
    port: Option<u16>,
    code: Option<String>,
}

pub struct BackendProcess {
    child: Child,
    stdin: Option<ChildStdin>,
    connection: BackendConnection,
}

impl BackendProcess {
    pub fn connection(&self) -> BackendConnection {
        self.connection.clone()
    }

    /// True once the process has terminated (crash, kill or exit).
    pub fn has_exited(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(Some(_)) | Err(_))
    }

    /// Close stdin (graceful stop), wait briefly, then kill as a last resort.
    pub fn shutdown(mut self) {
        drop(self.stdin.take());
        let deadline = Instant::now() + SHUTDOWN_GRACE;
        while Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => thread::sleep(Duration::from_millis(50)),
                Err(_) => break,
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn generate_token() -> Result<String, BackendError> {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).map_err(|_| BackendError::Random)?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

fn allowed_origins() -> Vec<&'static str> {
    #[allow(unused_mut)]
    let mut origins: Vec<&'static str> = TAURI_ORIGINS.to_vec();
    #[cfg(debug_assertions)]
    origins.extend(DEV_ORIGINS);
    origins
}

/// Dev builds run the backend from the repository via `uv`.
fn dev_command() -> Result<Command, BackendError> {
    let project = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../backend");
    let mut command = Command::new("uv");
    command
        .arg("run")
        .arg("--project")
        .arg(project)
        .args(["python", "-m", "pricepilot"]);
    Ok(command)
}

/// Release builds run the sidecar that Tauri bundles next to the executable
/// (`bundle.externalBin`; the target-triple suffix is removed when bundling).
fn sidecar_command() -> Result<Command, BackendError> {
    let exe = std::env::current_exe().map_err(|_| BackendError::NotPackaged)?;
    let dir = exe.parent().ok_or(BackendError::NotPackaged)?;
    let name = if cfg!(windows) {
        "pricepilot-api.exe"
    } else {
        "pricepilot-api"
    };
    let path = dir.join(name);
    if !path.is_file() {
        return Err(BackendError::NotPackaged);
    }
    #[allow(unused_mut)]
    let mut command = Command::new(path);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    Ok(command)
}

/// Both variants are compiled in every profile, so clippy checks the release path too.
fn backend_command() -> Result<Command, BackendError> {
    if cfg!(debug_assertions) {
        dev_command()
    } else {
        sidecar_command()
    }
}

fn stop_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

pub fn spawn(data_dir: &Path) -> Result<BackendProcess, BackendError> {
    std::fs::create_dir_all(data_dir).map_err(|_| BackendError::DataDir)?;
    let token = generate_token()?;

    let mut command = backend_command()?;
    command
        .args(["serve", "--port", "0", "--token-stdin", "--data-dir"])
        .arg(data_dir);
    for origin in allowed_origins() {
        command.arg("--allow-origin").arg(origin);
    }
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());

    let mut child = command.spawn().map_err(|_| BackendError::Spawn)?;
    let (Some(mut stdin), Some(stdout)) = (child.stdin.take(), child.stdout.take()) else {
        stop_child(&mut child);
        return Err(BackendError::Pipe);
    };
    if writeln!(stdin, "{token}")
        .and_then(|()| stdin.flush())
        .is_err()
    {
        stop_child(&mut child);
        return Err(BackendError::Pipe);
    }

    let (sender, receiver) = mpsc::channel::<Result<u16, BackendError>>();
    thread::spawn(move || {
        let mut pending = Some(sender);
        // Keep draining stdout for the whole process lifetime so the pipe never blocks.
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            let Some(tx) = pending.as_ref() else { continue };
            let Ok(event) = serde_json::from_str::<ProcessEvent>(&line) else {
                continue;
            };
            match (event.event.as_str(), event.port) {
                ("ready", Some(port)) => {
                    let _ = tx.send(Ok(port));
                    pending = None;
                }
                ("error", _) => {
                    let _ = tx.send(Err(BackendError::Refused(event.code.unwrap_or_default())));
                    pending = None;
                }
                _ => {}
            }
        }
        if let Some(tx) = pending {
            let _ = tx.send(Err(BackendError::Exited));
        }
    });

    match receiver.recv_timeout(READY_TIMEOUT) {
        Ok(Ok(port)) => Ok(BackendProcess {
            child,
            stdin: Some(stdin),
            connection: BackendConnection {
                base_url: format!("http://127.0.0.1:{port}"),
                token,
            },
        }),
        Ok(Err(error)) => {
            stop_child(&mut child);
            Err(error)
        }
        Err(_) => {
            stop_child(&mut child);
            Err(BackendError::Timeout)
        }
    }
}
