//! sandbox.rs — Rust mirror of `python/signalos_lib/sandbox.py`.
//!
//! Wraps subprocess invocations in `docker run` when the workspace has
//! sandbox mode enabled (`.signalos/sandbox.json` with `enabled: true`)
//! AND the Docker daemon is reachable. Falls back to running the command
//! unchanged otherwise, so call sites stay the same regardless of
//! sandbox state.
//!
//! Used by `runtime.rs::start_preview` to containerize `npm install` and
//! `npm run dev` so an LLM-generated app cannot ship malware in a
//! node-modules tree or bind raw sockets on the user's host.
//!
//! The preview path requires `host_network: true` because the user's
//! browser (and Playwright) must reach the dev server at 127.0.0.1 on
//! the host; explicit `-p` port mapping would also work but doesn't help
//! us until we know which port vite chose (it logs it).

use serde_json::Value;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

const DEFAULT_IMAGE_JS: &str = "node:20-alpine";
const DEFAULT_IMAGE_PY: &str = "python:3.11-slim";
const DEFAULT_IMAGE_SH: &str = "debian:bookworm-slim";

/// Options threaded through `maybe_wrap_for_sandbox` to control the
/// shape of the generated `docker run` argv.
///
/// `host_network=true` shares the host's network namespace with the
/// container. Defeats most of the blast-radius reduction the sandbox
/// is supposed to provide, but is needed for dev-server use cases
/// where the host must reach an unknown-in-advance port.
#[derive(Debug, Clone, Default)]
pub struct WrapOptions {
    pub image: Option<String>,
    pub ports: Vec<String>,
    pub host_network: bool,
}

/// Return true iff `docker info` returns 0 with non-empty stdout within
/// 3 seconds. Docker Desktop being installed but stopped doesn't count
/// — a `docker run` against a stopped daemon hangs.
pub fn docker_available() -> bool {
    docker_available_impl()
}

#[cfg(not(test))]
fn docker_available_impl() -> bool {
    real_docker_available()
}

#[cfg(test)]
fn docker_available_impl() -> bool {
    // In tests we want determinism: callers explicitly toggle via the
    // thread-local stub below.
    test_stub::docker_available_stub().unwrap_or_else(real_docker_available)
}

fn real_docker_available() -> bool {
    // std::process::Command doesn't have a built-in timeout; we spawn
    // the child, poll, and kill on timeout. 3 seconds matches the
    // Python implementation.
    let mut child = match Command::new("docker")
        .args(["info", "--format", "{{.ServerVersion}}"])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
    {
        Ok(c) => c,
        Err(_) => return false,
    };
    let deadline = std::time::Instant::now() + Duration::from_secs(3);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() {
                    return false;
                }
                let output = match child.wait_with_output() {
                    Ok(o) => o,
                    Err(_) => return false,
                };
                return !String::from_utf8_lossy(&output.stdout).trim().is_empty();
            }
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return false;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

/// Return the workspace's sandbox config or sane defaults. Schema
/// mirrors `python/signalos_lib/sandbox.py::get_sandbox_config` plus
/// the `image_sh` key (debian:bookworm-slim) for shell commands.
fn get_sandbox_config(workspace: &Path) -> Value {
    let path = workspace.join(".signalos").join("sandbox.json");
    let raw = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(_) => return defaults(),
    };
    let mut data: Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(_) => return defaults(),
    };
    if !data.is_object() {
        return defaults();
    }
    // Fill in missing keys with defaults.
    let map = data.as_object_mut().expect("checked is_object above");
    map.entry("enabled".to_string())
        .or_insert(Value::Bool(false));
    map.entry("image_js".to_string())
        .or_insert(Value::String(DEFAULT_IMAGE_JS.to_string()));
    map.entry("image_py".to_string())
        .or_insert(Value::String(DEFAULT_IMAGE_PY.to_string()));
    map.entry("image_sh".to_string())
        .or_insert(Value::String(DEFAULT_IMAGE_SH.to_string()));
    map.entry("extra_mounts".to_string())
        .or_insert(Value::Array(vec![]));
    data
}

fn defaults() -> Value {
    serde_json::json!({
        "enabled": false,
        "image_js": DEFAULT_IMAGE_JS,
        "image_py": DEFAULT_IMAGE_PY,
        "image_sh": DEFAULT_IMAGE_SH,
        "extra_mounts": [],
    })
}

/// Return true iff sandbox is configured on AND Docker is reachable.
/// Forbids enabling a non-functional sandbox: callers don't accidentally
/// wrap commands that would then fail to execute.
pub fn is_sandbox_enabled(workspace: &Path) -> bool {
    let cfg = get_sandbox_config(workspace);
    let enabled = cfg.get("enabled").and_then(Value::as_bool).unwrap_or(false);
    enabled && docker_available()
}

fn classify_command(cmd: &str) -> &'static str {
    // Strip directory + extension so "/usr/bin/python3.11" and
    // "C:\\Python\\python.exe" both classify as "py".
    let base = std::path::Path::new(cmd)
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| cmd.to_string());
    let lower = base.to_lowercase();
    let stem = lower.strip_suffix(".exe").unwrap_or(&lower);
    match stem {
        "python" | "python3" | "pytest" | "pip" | "uv" => "py",
        "bash" | "sh" | "dash" | "zsh" => "sh",
        "npm" | "node" | "npx" | "pnpm" | "yarn" => "js",
        s if s.starts_with("python") => "py",
        _ => "js",
    }
}

fn image_for(cfg: &Value, cmd: &str, override_image: Option<&str>) -> String {
    if let Some(img) = override_image {
        return img.to_string();
    }
    let key = match classify_command(cmd) {
        "py" => "image_py",
        "sh" => "image_sh",
        _ => "image_js",
    };
    cfg.get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| match key {
            "image_py" => DEFAULT_IMAGE_PY.to_string(),
            "image_sh" => DEFAULT_IMAGE_SH.to_string(),
            _ => DEFAULT_IMAGE_JS.to_string(),
        })
}

/// Construct the `docker run` (bin, argv) tuple that runs *cmd* inside
/// a container. Equivalent shape to
/// `python/signalos_lib/sandbox.py::build_docker_run_argv` plus
/// optional `--network host` for dev-server use cases.
///
/// Mount + isolation strategy:
///   - Workspace -> /workspace (rw)
///   - WORKDIR set to /workspace
///   - --rm so the container goes away when the command exits
///   - Default bridge networking; host_network=true opts into
///     `--network host` (caller's responsibility to know it's safe).
///   - Named ports forwarded via `-p host:container`.
pub fn build_docker_run_argv(
    workspace: &Path,
    cmd: &str,
    args: &[String],
    opts: WrapOptions,
) -> (String, Vec<String>) {
    let cfg = get_sandbox_config(workspace);
    let image = image_for(&cfg, cmd, opts.image.as_deref());
    let workspace_abs = workspace
        .canonicalize()
        .unwrap_or_else(|_| workspace.to_path_buf());
    let mut argv: Vec<String> = vec![
        "run".to_string(),
        "--rm".to_string(),
        "-i".to_string(),
        "-v".to_string(),
        format!("{}:/workspace", workspace_abs.display()),
        "-w".to_string(),
        "/workspace".to_string(),
    ];
    if opts.host_network {
        argv.push("--network".to_string());
        argv.push("host".to_string());
    }
    for p in &opts.ports {
        argv.push("-p".to_string());
        argv.push(p.clone());
    }
    if let Some(extras) = cfg.get("extra_mounts").and_then(Value::as_array) {
        for m in extras {
            if let Some(s) = m.as_str() {
                argv.push("-v".to_string());
                argv.push(s.to_string());
            }
        }
    }
    argv.push(image);
    argv.push(cmd.to_string());
    for a in args {
        argv.push(a.clone());
    }
    ("docker".to_string(), argv)
}

/// Wrap *cmd* in `docker run` if sandboxed mode is enabled for this
/// workspace. Returns (bin, args, was_wrapped). Callers stay the same
/// regardless of sandbox state — just feed `bin` and `args` into
/// `Command::new(bin).args(args)`. When `was_wrapped` is true, the
/// orchestrator can surface a "(sandboxed)" hint to the UI.
///
/// Falls back to (cmd, args, false) when:
///   - Sandbox mode is off in `.signalos/sandbox.json`
///   - Docker isn't installed / daemon isn't running
pub fn maybe_wrap_for_sandbox(
    workspace: &Path,
    cmd: &str,
    args: &[String],
    opts: WrapOptions,
) -> (String, Vec<String>, bool) {
    if is_sandbox_enabled(workspace) {
        let (bin, argv) = build_docker_run_argv(workspace, cmd, args, opts);
        (bin, argv, true)
    } else {
        (cmd.to_string(), args.to_vec(), false)
    }
}

// ─── Test-only stub for docker_available ──────────────────────────────────────
//
// The Python tests use `unittest.mock.patch` to override
// `docker_available`; Rust doesn't have monkey-patching. We expose a
// thread-local override that test code can set via the `set_docker_stub`
// helper. `docker_available_impl` (which is `#[cfg(test)]`-gated) reads
// this before falling back to the real probe.

#[cfg(test)]
mod test_stub {
    use std::cell::Cell;

    thread_local! {
        static DOCKER_STUB: Cell<Option<bool>> = const { Cell::new(None) };
    }

    pub fn docker_available_stub() -> Option<bool> {
        DOCKER_STUB.with(|c| c.get())
    }

    pub fn set_docker_stub(val: Option<bool>) {
        DOCKER_STUB.with(|c| c.set(val));
    }
}

// ─── Unit tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// Build a unique tempdir under std::env::temp_dir() with the
    /// given suffix. Cleaned up by the test on drop via the returned
    /// guard.
    struct TempWorkspace {
        path: PathBuf,
    }

    impl TempWorkspace {
        fn new(tag: &str) -> Self {
            let ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0);
            let path = std::env::temp_dir().join(format!("signalos-sandbox-{tag}-{ts}"));
            std::fs::create_dir_all(&path).expect("create tempdir");
            Self { path }
        }

        fn path(&self) -> &Path {
            &self.path
        }

        fn write_sandbox_json(&self, body: &str) {
            let dir = self.path.join(".signalos");
            std::fs::create_dir_all(&dir).expect("create .signalos");
            std::fs::write(dir.join("sandbox.json"), body).expect("write sandbox.json");
        }
    }

    impl Drop for TempWorkspace {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }

    fn index_of(argv: &[String], needle: &str) -> Option<usize> {
        argv.iter().position(|s| s == needle)
    }

    #[test]
    fn test_docker_argv_shape_default() {
        let tw = TempWorkspace::new("shape");
        let (bin, argv) = build_docker_run_argv(
            tw.path(),
            "echo",
            &["hello".to_string()],
            WrapOptions::default(),
        );
        assert_eq!(bin, "docker");
        assert_eq!(argv[0], "run");
        assert!(argv.contains(&"--rm".to_string()));
        assert!(
            !argv.contains(&"--network".to_string()),
            "default argv must not contain --network",
        );
        // Workspace -> /workspace mount present.
        let v_idx = index_of(&argv, "-v").expect("has -v flag");
        assert!(
            argv[v_idx + 1].ends_with(":/workspace"),
            "mount target should end with :/workspace, got {}",
            argv[v_idx + 1],
        );
        // WORKDIR set.
        let w_idx = index_of(&argv, "-w").expect("has -w flag");
        assert_eq!(argv[w_idx + 1], "/workspace");
        // Command appended at the end.
        assert_eq!(argv.last().map(String::as_str), Some("hello"));
        assert_eq!(argv[argv.len() - 2], "echo");
    }

    #[test]
    fn test_host_network_opt_in() {
        let tw = TempWorkspace::new("hostnet");
        let (_, argv) = build_docker_run_argv(
            tw.path(),
            "npm",
            &["run".to_string(), "dev".to_string()],
            WrapOptions {
                host_network: true,
                ..Default::default()
            },
        );
        let n_idx = index_of(&argv, "--network").expect("has --network flag");
        assert_eq!(argv[n_idx + 1], "host");
    }

    #[test]
    fn test_ports_emit_p_flags() {
        let tw = TempWorkspace::new("ports");
        let (_, argv) = build_docker_run_argv(
            tw.path(),
            "npm",
            &["run".to_string(), "dev".to_string()],
            WrapOptions {
                ports: vec!["5173:5173".to_string(), "9229:9229".to_string()],
                ..Default::default()
            },
        );
        // Both ports appear, each preceded by a -p flag.
        for port in ["5173:5173", "9229:9229"] {
            let idx = index_of(&argv, port).unwrap_or_else(|| panic!("argv missing {port}"));
            assert_eq!(argv[idx - 1], "-p", "{port} must follow a -p flag");
        }
    }

    #[test]
    fn test_classifies_npm_to_image_js() {
        let tw = TempWorkspace::new("npmjs");
        tw.write_sandbox_json(r#"{"enabled": false, "image_js": "node:21-alpine"}"#);
        let (_, argv) = build_docker_run_argv(
            tw.path(),
            "npm",
            &["install".to_string()],
            WrapOptions::default(),
        );
        assert!(
            argv.contains(&"node:21-alpine".to_string()),
            "npm should pick image_js (node:21-alpine), got argv: {argv:?}",
        );
    }

    #[test]
    fn test_classifies_python_to_image_py() {
        let tw = TempWorkspace::new("pyimg");
        tw.write_sandbox_json(r#"{"enabled": false, "image_py": "python:3.12-slim"}"#);
        let (_, argv) = build_docker_run_argv(
            tw.path(),
            "python",
            &["-m".to_string(), "pytest".to_string()],
            WrapOptions::default(),
        );
        assert!(
            argv.contains(&"python:3.12-slim".to_string()),
            "python should pick image_py, got argv: {argv:?}",
        );
    }

    #[test]
    fn test_classifies_bash_to_image_sh() {
        let tw = TempWorkspace::new("shimg");
        tw.write_sandbox_json(r#"{"enabled": false, "image_sh": "alpine:3.20"}"#);
        let (_, argv) = build_docker_run_argv(
            tw.path(),
            "bash",
            &["-c".to_string(), "echo hi".to_string()],
            WrapOptions::default(),
        );
        assert!(
            argv.contains(&"alpine:3.20".to_string()),
            "bash should pick image_sh, got argv: {argv:?}",
        );
    }

    #[test]
    fn test_no_sandbox_json_means_disabled() {
        let tw = TempWorkspace::new("nofile");
        // Belt-and-suspenders: even if docker is up, no sandbox.json
        // means disabled.
        test_stub::set_docker_stub(Some(true));
        let enabled = is_sandbox_enabled(tw.path());
        test_stub::set_docker_stub(None);
        assert!(!enabled);
    }

    #[test]
    fn test_sandbox_enabled_requires_docker() {
        let tw = TempWorkspace::new("needsdocker");
        tw.write_sandbox_json(r#"{"enabled": true}"#);
        // Even with the flag on, docker missing means disabled.
        test_stub::set_docker_stub(Some(false));
        let enabled_no_docker = is_sandbox_enabled(tw.path());
        // With docker present AND the flag on, sandbox is enabled.
        test_stub::set_docker_stub(Some(true));
        let enabled_with_docker = is_sandbox_enabled(tw.path());
        test_stub::set_docker_stub(None);
        assert!(!enabled_no_docker);
        assert!(enabled_with_docker);
    }

    #[test]
    fn test_maybe_wrap_when_disabled_returns_unchanged() {
        let tw = TempWorkspace::new("unwrapped");
        let (bin, argv, wrapped) = maybe_wrap_for_sandbox(
            tw.path(),
            "npm",
            &["install".to_string()],
            WrapOptions::default(),
        );
        assert!(!wrapped);
        assert_eq!(bin, "npm");
        assert_eq!(argv, vec!["install".to_string()]);
    }

    #[test]
    fn test_maybe_wrap_when_enabled_wraps() {
        let tw = TempWorkspace::new("wrapped");
        tw.write_sandbox_json(r#"{"enabled": true}"#);
        test_stub::set_docker_stub(Some(true));
        let (bin, argv, wrapped) = maybe_wrap_for_sandbox(
            tw.path(),
            "npm",
            &["install".to_string()],
            WrapOptions::default(),
        );
        test_stub::set_docker_stub(None);
        assert!(wrapped);
        assert_eq!(bin, "docker");
        // npm + install are the final two argv elements.
        assert_eq!(argv[argv.len() - 2], "npm");
        assert_eq!(argv[argv.len() - 1], "install");
    }

    #[test]
    fn test_extra_mounts_passed_through() {
        let tw = TempWorkspace::new("mounts");
        tw.write_sandbox_json(r#"{"enabled": false, "extra_mounts": ["/host/cache:/cache:ro"]}"#);
        let (_, argv) = build_docker_run_argv(
            tw.path(),
            "npm",
            &["test".to_string()],
            WrapOptions::default(),
        );
        assert!(argv.contains(&"/host/cache:/cache:ro".to_string()));
    }

    #[test]
    fn test_corrupt_sandbox_json_falls_back_to_defaults() {
        let tw = TempWorkspace::new("corrupt");
        tw.write_sandbox_json("not valid json");
        // Should not panic and should treat sandbox as disabled.
        test_stub::set_docker_stub(Some(true));
        let enabled = is_sandbox_enabled(tw.path());
        test_stub::set_docker_stub(None);
        assert!(!enabled);
    }

    #[test]
    fn test_image_override_wins() {
        let tw = TempWorkspace::new("override");
        let (_, argv) = build_docker_run_argv(
            tw.path(),
            "npm",
            &["install".to_string()],
            WrapOptions {
                image: Some("my-custom:latest".to_string()),
                ..Default::default()
            },
        );
        assert!(argv.contains(&"my-custom:latest".to_string()));
        assert!(!argv.contains(&"node:20-alpine".to_string()));
    }
}
