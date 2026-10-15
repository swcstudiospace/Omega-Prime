# Phase 96 - Context

**Requirement:** INST-01 through INST-07.

**Evidence:** No Notion client existed. Linear findings sync posted `issueCreate` through `gh api graphql`. Gateways already in the process are substrate (`SUBSTRATE_URL`), the red-team loopback (`redteam.gateway_url`), and the desk bus (`OMEGA_PRIME_DESK_BUS_URL`). `.gitmodules` already pins `prime-agent` and `redteam`. Tenancy is one install config. Secrets already enter through host env or a mode 0600 file (`read_secret_file`). The chat surface is `/omega-connectors`.

**Rejected:** A second auth method, a hosted multi-tenant control plane, a new gateway protocol, silent Notion property mapping, and submodule remotes the setup docs do not require (`hermes-agent`, `oh-my-pi`, `programming-desk`, `agent-substrate`, `claude-ultrathink`, `vps-agent-bus`).

**Assumption:** The Tasks database shape is the dashboard contract. The question UI could not ask the operator to confirm that against the Agent Task Graph.
