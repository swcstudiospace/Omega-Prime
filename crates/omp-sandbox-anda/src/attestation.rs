//! Attestation. Locally this is ALWAYS a mock: there is no TEE (no SEV/TDX/SGX/Nitro) on this box.
//! The `tee` feature is a seam for IC-TEE / a confidential VM; it still refuses to claim hardware.
use serde_json::{Value, json};

pub fn mode() -> &'static str { "mock" }

pub fn stamp(mut v: Value) -> Value {
    if let Some(o) = v.as_object_mut() { o.insert("attestation".into(), json!(mode())); }
    v
}
