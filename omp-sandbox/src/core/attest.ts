// Mock attestation. There is no TEE hardware on this box (no SEV/TDX/SGX/Nitro), so every document
// says kind=mock and every receipt carries attestation="mock". The real path (Anda + IC-TEE on AWS
// Nitro, OpenShell on a confidential VM) is a seam in the Rust wrapper's `tee` feature, not built here.
import { createHash, randomBytes } from "node:crypto";
import { aedt, VERSIONS } from "./config.ts";

export type AttestationDoc = {
  attestation: "mock"; kind: "mock"; hardware: false; tier: string;
  measurement: string; nonce: string; issuedAt: string; warning: string;
};

export function mockAttestation(tier: string, extra: Record<string, unknown> = {}): AttestationDoc {
  const measured = JSON.stringify({ tier, versions: VERSIONS, ...extra });
  return {
    attestation: "mock", kind: "mock", hardware: false, tier,
    measurement: "sha256:" + createHash("sha256").update(measured).digest("hex"),
    nonce: randomBytes(16).toString("hex"), issuedAt: aedt(),
    warning: "NOT hardware-attested: local mock. Real dTEE needs IC-TEE (AWS Nitro) or a confidential VM.",
  };
}
