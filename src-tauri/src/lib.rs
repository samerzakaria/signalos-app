/// lib.rs — re-exports all modules so `cargo test --lib` can reach the unit tests
/// in ipc.rs, keychain.rs, provider.rs, etc.
///
/// main.rs stays thin (just wires Tauri and calls build_menu).
/// All testable logic lives in these modules.
pub mod enforcement;
pub mod governance;
pub mod ipc;
pub mod keychain;
pub mod provider;
pub mod runtime;
pub mod sandbox;
pub mod sidecar;
pub mod test_automation;
