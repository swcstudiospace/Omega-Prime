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
        // Write the whole secret to a private temp file, then publish it with hard_link (fails if `name` exists).
        // A competing creator that wins the race is not an error: both then read the one published secret, and a
        // reader never sees a half-written file.
        let mut s = [0u8; 32];
        rand::fill(&mut s);
        let tmp = dir.join(format!(".{name}.{}.{}", std::process::id(), unique()));
        let res = (|| {
            let mut f = fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp).map_err(|e| format!("create {name}: {e}"))?;
            f.write_all(&s).and_then(|_| f.sync_all()).map_err(|e| e.to_string())?;
            match fs::hard_link(&tmp, &p) {
                Ok(()) => Ok(()),
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
                Err(e) => Err(format!("publish {name}: {e}")),
            }
        })();
        let _ = fs::remove_file(&tmp);
        res?;
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

fn unique() -> String { let mut b = [0u8; 8]; rand::fill(&mut b); b.iter().map(|x| format!("{x:02x}")).collect() }

/// Replace `dir/name` with `contents` atomically (temp file + rename): a concurrent reader sees the old or the
/// new contents, never an empty or partial file.
pub fn publish_atomic(dir: &std::path::Path, name: &str, contents: &str) -> Result<(), String> {
    let tmp = dir.join(format!(".{name}.{}.{}", std::process::id(), unique()));
    let res = (|| {
        let mut f = fs::OpenOptions::new().write(true).create_new(true).mode(0o644).open(&tmp).map_err(|e| e.to_string())?;
        f.write_all(contents.as_bytes()).and_then(|_| f.sync_all()).map_err(|e| e.to_string())?;
        fs::rename(&tmp, dir.join(name)).map_err(|e| e.to_string())
    })();
    if res.is_err() { let _ = fs::remove_file(&tmp); }
    res
}

/// Create the client key if needed and publish its principal (public) for the server.
pub async fn client() -> Result<Arc<Web3Client>, String> {
    let dir = ensure_dir().map_err(|e| e.to_string())?;
    let c = web3_from_secret(load_or_create_secret_in(&dir, "client.ed25519")?).await?;
    publish_atomic(&dir, "client.principal", &c.get_principal().to_text())?;
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
    fn parallel_creators_agree_on_one_secret() {
        let d = tmp("race");
        let hs: Vec<_> = (0..16).map(|_| { let d = d.clone(); std::thread::spawn(move || load_or_create_secret_in(&d, "k")) }).collect();
        let got: Vec<[u8; 32]> = hs.into_iter().map(|h| h.join().unwrap().expect("no creator fails on AlreadyExists")).collect();
        assert!(got.windows(2).all(|w| w[0] == w[1]), "every creator reads the one published secret");
        assert_eq!(fs::metadata(d.join("k")).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(fs::read_dir(&d).unwrap().count(), 1, "no temp files left behind");
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn principal_is_never_seen_empty_while_republished() {
        let d = tmp("pub");
        publish_atomic(&d, "p", "aaaaa-aa").unwrap();
        let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let (d2, s2) = (d.clone(), stop.clone());
        let reader = std::thread::spawn(move || { let mut n = 0; while !s2.load(std::sync::atomic::Ordering::SeqCst) { assert_eq!(fs::read_to_string(d2.join("p")).unwrap(), "aaaaa-aa"); n += 1; } n });
        for _ in 0..500 { publish_atomic(&d, "p", "aaaaa-aa").unwrap(); }
        stop.store(true, std::sync::atomic::Ordering::SeqCst);
        assert!(reader.join().unwrap() > 0);
        assert_eq!(fs::read_dir(&d).unwrap().count(), 1);
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
