# Requirements: v19 Installer bindings

Accepted 2026-10-15 from the request to let any clone use its own Linear,
Notion, and optional gateway. The question UI was unavailable, so the
defaults below are the contract. Phase 77.1 live UAT stays user-owned.

Defaults, because they were not already in the repository:

- Notion contract version 1 is the Tasks database shape (Name, Status with
  Not started / Up next / In progress / Done, optional Due date and Project).
  No live workspace id is shipped. This is not the Agent Task Graph.
- "VPS gateway" means the three optional endpoints the bot already has:
  substrate, the red-team gateway, and the desk bus. No new protocol.
- Submodules stay `prime-agent` (required) and `redteam` (optional). Repos
  that setup docs do not require were not added.
- One binding per install. There is no second user record inside one process.

- [x] **INST-01**: `/omega-connectors` with no binding starts at the Notion template, then Notion, then Linear, then an optional gateway. Quitting and returning resumes the incomplete step.
- [x] **INST-02**: Notion binds only a database that matches contract version 1. A missing required property or an unshared database stays unready and names the recovery.
- [x] **INST-03**: Linear uses the host token against Linear's API. One team is stored. Several teams require an explicit choice. A rejected token stays invalid.
- [x] **INST-04**: Findings sync uses that Linear client when a token is set. With no token, the previous `gh` path is unchanged.
- [x] **INST-05**: Skipping gateways leaves Notion and Linear usable. A skipped red-team gateway returns not-configured and does not open a socket. Timeout is degraded. Auth failure is invalid.
- [x] **INST-06**: An empty `prime-agent` checkout fails setup check with `git submodule update --init --recursive`.
- [x] **INST-07**: Tokens are not stored in the binding file, not echoed by `/omega-connectors`, and not accepted when pasted into the command.
