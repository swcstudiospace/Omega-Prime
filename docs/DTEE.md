# dTEE status

Locally there is **no TEE**. Every receipt and every wrapper line says `attestation: "mock"`. `mockAttestation()` produces a sha256 "measurement" of the tier name, isolation and versions, plus a nonce. The output says `hardware:false`.

What a real dTEE would need:

- **Anda / IC-TEE:** an AWS Nitro Enclave host running the IC-TEE stack (ic_tee_cdk, ic_tee_nitro_attestation).
  - The `tee` feature in `crates/omp-sandbox-anda` would fetch the attestation document and verify it.
  - The real Anda engine is already wired (anda_engine 0.17.4 on rustc 1.99.1). What is missing is the TEE host: today the engine uses the non-TEE `anda_web3_client` and attestation stays `mock`.
- **Confidential VM:** SEV-SNP or TDX. This alternative runs the OpenShell MicroVM tier inside a CVM and attests the CVM report.
- In both cases, the auth-broker would move inside the trust boundary or behind attested TLS. Today it runs on the host.

Nothing here is hardware-attested. Treat it as a development sandbox.
