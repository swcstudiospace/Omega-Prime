//! Local Ed25519 identities for the Anda access layer. Raw 32-byte secrets live at mode 0600 under
//! ~/.local/state/omp-sandbox-anda (dir 0700) and are never printed; only principals are.
//!   client.ed25519 + client.principal : the caller allowed to use the sandbox (engine controller)
//!   engine.ed25519                    : the engine's own identity (its principal = engine id)
use anda_web3_client::client::{Client as Web3Client, identity_from_secret};
use candid::Principal;
use std::fs;
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::PathBuf;
use std::sync::Arc;

pub fn state_dir() -> PathBuf {
    if let Ok(d) = std::env::var("OMP_SANDBOX_ANDA_STATE") { return PathBuf::from(d); }
    PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".local/state/omp-sandbox-anda")
}

fn ensure_dir() -> std::io::Result<PathBuf> {
    let d = state_dir();
    fs::create_dir_all(&d)?;
    fs::set_permissions(&d, fs::Permissions::from_mode(0o700))?;
    Ok(d)
}

/// Load a 32-byte secret, creating it (0600, O_EXCL) if missing. Refuses group/world-readable files.
pub fn load_or_create_secret(name: &str) -> Result<[u8; 32], String> {
    load_or_create_secret_in(&ensure_dir().map_err(|e| e.to_string())?, name)
}

pub fn load_or_create_secret_in(dir: &std::path::Path, name: &str) -> Result<[u8; 32], String> {
    let p = dir.join(name);
    if !p.exists() {
        let mut s = [0u8; 32];
        rand::fill(&mut s);
        let mut f = fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&p).map_err(|e| format!("create {name}: {e}"))?;
        f.write_all(&s).map_err(|e| e.to_string())?;
    }
    let meta = fs::metadata(&p).map_err(|e| e.to_string())?;
    if meta.permissions().mode() & 0o077 != 0 { return Err(format!("{name} must be mode 0600 (refusing to use it)")); }
    let b = fs::read(&p).map_err(|e| e.to_string())?;
    b.as_slice().try_into().map_err(|_| format!("{name}: expected 32 bytes"))
}

pub async fn web3_from_secret(secret: [u8; 32]) -> Result<Arc<Web3Client>, String> {
    let id = identity_from_secret(secret);
    Web3Client::builder().with_identity(Arc::from(id)).with_allow_http(true).build().await.map(Arc::new).map_err(|e| e.to_string())
}

/// Create the client key if needed and publish its principal (public) for the server.
pub async fn client() -> Result<Arc<Web3Client>, String> {
    let c = web3_from_secret(load_or_create_secret("client.ed25519")?).await?;
    let pf = state_dir().join("client.principal");
    fs::write(&pf, c.get_principal().to_text()).map_err(|e| e.to_string())?;
    Ok(c)
}

/// The server only reads the client's *principal*, never its secret.
pub fn client_principal() -> Result<Principal, String> {
    let t = fs::read_to_string(state_dir().join("client.principal")).map_err(|e| format!("client.principal missing (run `omp-sandbox-anda keygen`): {e}"))?;
    Principal::from_text(t.trim()).map_err(|e| e.to_string())
}

pub async fn engine() -> Result<Arc<Web3Client>, String> { web3_from_secret(load_or_create_secret("engine.ed25519")?).await }

/// Throwaway identity for negative tests (never written to disk).
pub async fn ephemeral() -> Result<Arc<Web3Client>, String> {
    let mut s = [0u8; 32];
    rand::fill(&mut s);
    web3_from_secret(s).await
}

#[cfg(test)]
mod tests {
    use super::*;
    fn tmp(tag: &str) -> PathBuf { let d = std::env::temp_dir().join(format!("osa-keys-{tag}-{}", std::process::id())); let _ = fs::remove_dir_all(&d); fs::create_dir_all(&d).unwrap(); d }

    #[test]
    fn creates_0600_and_is_stable() {
        let d = tmp("ok");
        let a = load_or_create_secret_in(&d, "k").unwrap();
        assert_eq!(fs::metadata(d.join("k")).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(a, load_or_create_secret_in(&d, "k").unwrap());
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn refuses_group_or_world_readable() {
        let d = tmp("perm");
        load_or_create_secret_in(&d, "k").unwrap();
        fs::set_permissions(d.join("k"), fs::Permissions::from_mode(0o644)).unwrap();
        assert!(load_or_create_secret_in(&d, "k").unwrap_err().contains("0600"));
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn refuses_wrong_length() {
        let d = tmp("len");
        fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(d.join("k")).unwrap().write_all(b"short").unwrap();
        assert!(load_or_create_secret_in(&d, "k").is_err());
        fs::remove_dir_all(&d).unwrap();
    }
}
