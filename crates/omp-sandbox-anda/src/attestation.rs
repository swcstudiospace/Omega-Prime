//! Attestation seam. Locally this is ALWAYS a mock: no hardware evidence exists.
use serde_json::{json, Value};

pub fn mode() -> &'static str {
    if cfg!(feature = "tee") {
        // Feature seam: a real implementation would fetch a Nitro/IC-TEE attestation document
        // here. Until it exists we still refuse to claim hardware attestation.
        "mock"
    } else {
        "mock"
    }
}

pub fn stamp(mut v: Value) -> Value {
    if let Some(o) = v.as_object_mut() {
        o.insert("attestation".into(), json!(mode()));
    }
    v
}
