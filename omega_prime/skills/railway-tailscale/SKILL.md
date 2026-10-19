---
name: railway-tailscale
description: Reach the Railway data planes over Tailscale Forwarders, cut the VPS over, retire public endpoints under G-6, and roll back.
bots: [bot-00-omega-prime]
gates: [G-5, G-6]
---
<skill name="railway-tailscale" version="1">
# Railway over Tailscale

## Served tools

Use the Omega Prime tools named below (`lead_*`, `app_*`, `web_*`, `mob_*`, `infra_*`, `sys_*`, `qua_*`). An external Desk Gateway is optional and is not part of a default install. Its `desk_*` and `agent_bus_*` tools are a fallback only when that gateway is connected and a served tool returns `not_configured`. If neither answers, stop and record the blocker. Do not invent a tool name.

## When this applies (L1)

INFRA only. The five Railway services (Ultrathink: GreptimeDB, TimescaleDB, DragonflyDB; Agent
Substrate: Hindsight, RAGFlow) are reached from the VPS over the tailnet by MagicDNS name, through
a **Tailscale Forwarder** deployed inside each Railway project. Railway private networking is per
project and per environment, so one forwarder per project is the minimum and there is no
cross-project shortcut. After cutover the public TCP proxy and public database domains are
retired (G-6). Bot computers never join the tailnet; they reach the gateway over normal egress.

```
Standing up or changing the Railway ↔ VPS path?
│
├─ New project or environment ──▶ One forwarder per project/environment. §1–§2
├─ Adding a service to reach ──▶ New CONNECTION_MAPPING_n on that project's forwarder. §2
├─ ACL change ──▶ Tags only: tag:railway-forwarder, tag:vps, tag:admin. §3
├─ Cutting the VPS over ──▶ Verify (infra_tailscale_status, infra_db_health) → env → restart →
│                           verify again → retire public exposure under G-6 with approval. §4–§5
├─ Something broke after cutover ──▶ Roll back: revert env by file, restart; re-create the proxy
│                                     or domain if it was already removed. §6
└─ Writing the receipt ──▶ §7. TS_AUTHKEY and connection strings never appear in it.
```

Nothing in this skill is deployed yet; service names and ports are provisional until the n1
inventory records them. Treat every value below as "to confirm", not "confirmed".

---

## Method (L2)

### §1 Why one forwarder per project

`*.railway.internal` resolves only inside the project and environment that owns the service, and
not from the VPS at all (the substrate env comment records this). A forwarder is a Railway service
that joins the tailnet and proxies listed ports to `<service>.railway.internal`. Two projects,
two forwarders; a second environment (staging) is a third.

### §2 Forwarder configuration

Deploy Railway's Tailscale Forwarder template (it replaced the deprecated subnet-router template)
into each project. Variables:

| Variable | Value | Consequence if wrong |
|---|---|---|
| `TS_AUTHKEY` | A **reusable, tagged** key from the Tailscale admin console, tag `tag:railway-forwarder`, ephemeral **off**, key expiry disabled on the resulting machine | A one-off key breaks the next redeploy; an ephemeral node vanishes on restart and every mapping with it; an untagged node gets no ACL grant |
| `CONNECTION_MAPPING_n` | `<src_port>:<service>.railway.internal:<port>`, one per service, `n` = 1, 2, … | A typo in the service name is a mapping to nothing; the port probe in §4 is how you find it |
| Volume | Mounted for Tailscale state, so the machine identity survives redeploys | Without it each redeploy is a new machine with a new name and the VPS env points at a ghost |

Machine name: `<project>-<environment>-tailscale-forwarder`. The VPS env uses these names, never
the tailnet IP, which changes when the node is re-created. Expected mappings (provisional;
confirm names and ports in n1 before deploying):

| Forwarder | Mappings |
|---|---|
| `{{TAILSCALE_FORWARDER_DATA}}` | `4000:{{RAILWAY_GREPTIME_HOST}}:4000` (HTTP SQL), `4001:…:4001` (gRPC), `4003:…:4003` (PG wire), `5432:{{RAILWAY_TIMESCALE_HOST}}:5432`, `6379:{{RAILWAY_DRAGONFLY_HOST}}:6379` |
| `{{TAILSCALE_FORWARDER_APPS}}` | `8888:{{RAILWAY_HINDSIGHT_HOST}}:8888`, `9380:{{RAILWAY_RAGFLOW_HOST}}:9380`, optionally `80:{{RAILWAY_RAGFLOW_HOST}}:80` |

Traffic inside the tailnet is WireGuard-encrypted, so plaintext service ports on the forwarder hop
are acceptable. Hindsight and RAGFlow still require their bearer keys: the tailnet replaces the
public exposure, not the application auth.

### §3 ACL

| Tag | Applied to |
|---|---|
| `tag:railway-forwarder` | Both forwarders (via the auth key) |
| `tag:vps` | The VPS |
| `tag:admin` | The operator's admin devices (`{{ADMIN_DEVICES}}`) |

Grants: `tag:vps → tag:railway-forwarder` on the mapped ports; `tag:admin → tag:railway-forwarder`
on the same; nothing else. Deny by default. A grant to `*` or to an untagged node is an ACL
change QUALITY should see before it is applied, because it widens who can reach a database.

Check the `{{TAILSCALE_LEGACY_NODE_NAME}}` node ({{TAILSCALE_LEGACY_NODE}}) in n1: a deprecated subnet router or exit node is
retired under G-6 after the forwarders are verified; an existing forwarder is adopted, not duplicated.

### §4 Verify before touching the VPS

1. `infra_tailscale_status` — both forwarders online, this node tagged `tag:vps`, mapped ports reachable.
2. From the VPS shell (`skills/remote-dev-machine`; SSH is INFRA-owned ops): `psql` to
   `{{TAILSCALE_FORWARDER_DATA}}:5432`, `curl` to `:4000` and to
   `{{TAILSCALE_FORWARDER_APPS}}:8888/health` and `:9380`. Record each exit code.
3. `infra_db_health` — five services green over the tailnet names, before any env changes.

If any probe fails here, stop. The cutover has nothing to cut over to yet.

### §5 Cutover order

1. Edit `{{VPS_SUBSTRATE_ENV}}` and `{{VPS_GATEWAY_ENV}}`: `GREPTIME_URL`,
   `SUBSTRATE_PG_URL`, `DRAGONFLY_URL`, `HINDSIGHT_URL`, `RAGFLOW_URL` to the forwarder names.
   Keep a dated copy of each file beside it; that copy is the rollback.
2. Restart `{{VPS_UNIT_SUBSTRATE}}` and `{{VPS_UNIT_GATEWAY}}`. `infra_vps_units` shows them active.
3. Verify again: `infra_db_health` green; `lead_event_emit` lands; `lead_brief` returns. Record
   the calls.
4. Only now retire public exposure (G-6): delete the TimescaleDB TCP proxy and the Greptime
   public domain, in the Railway dashboard or CLI. There is deliberately no gateway tool for
   this; it is a human-approved destructive change with an `approval_id` obtained through LEAD
   and recorded in the receipt. Keep `{{RAILWAY_BROWSER_SERVICE}}` on a Railway domain behind its access key
   only if the operator wants browser access without the tailnet.
5. Verify a third time. A service still reachable on its public address is a step 4 that did not
   finish.

Steps 1–3 before step 4, always: retiring the proxy first leaves the substrate pointing at a host
that no longer answers, and the outage lasts until someone notices.

### §6 Rollback

| Symptom | Action | Time | Data |
|---|---|---|---|
| `infra_db_health` red after restart | Restore the dated env copies; restart the units | Minutes | None |
| Public proxy or domain already removed and needed back | Re-create the TCP proxy / domain in Railway; the new proxy has a **new** host and port, so update the env accordingly | Minutes | None; the database did not move |
| Forwarder unhealthy | Redeploy it (the volume keeps its identity); if it re-registers as a new machine, the volume was missing | Minutes | None |

None of these has a data implication; the services never moved. Say so in the receipt (G-5 asks).

### §7 The receipt

`.receipts/bot-05-infrastructure/<task>.json` with: `infra_tailscale_status` output tail; each port
probe with exit code; `infra_db_health` before and after; the env diff **by variable name only**
(never the values); `infra_vps_units` after restart; the Railway proxy/domain removal (CLI output
or screenshot reference); `approvals` with the G-6 approval id; `rollback_plan` from §6;
`unverified` for anything not exercised (staging environment, `{{TAILSCALE_LEGACY_NODE_NAME}}` disposition, ACL
from the admin machines if not probed). `TS_AUTHKEY`, database passwords and bearer keys are
never in the receipt, a template, a message or a tool argument (G-3, PD-4).

---

## Worked example

**Good — ordering that survives a bad mapping.**

```
infra_tailscale_status → {{TAILSCALE_FORWARDER_APPS}} online; port 9380 unreachable
curl {{TAILSCALE_FORWARDER_APPS}}:9380 → exit 7          ← mapping typo found
fix CONNECTION_MAPPING_2 in Railway → redeploy forwarder → curl → exit 0
infra_db_health → 5/5 green (public addresses still in env; nothing cut over yet)
edit env (dated copies kept) → restart → infra_vps_units active → infra_db_health 5/5
approval via LEAD → {{APPROVAL_ID}} → delete TCP proxy + Greptime domain → infra_db_health 5/5
receipt: probes, env names, approval id, rollback plan, unverified: ["staging not built"]
```

**Bad — same change, proxy first.**

```
delete TimescaleDB TCP proxy ("we're moving anyway") → substrate-mcp loses Timescale →
intake queue and claims unavailable for 40 minutes while the forwarder mapping is debugged.
```

Nothing was lost, and the desk was down for the whole debugging session because the old path was
removed before the new one was proven. The order in §5 exists for this case.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| One forwarder for two projects | Second project's services never resolve | One forwarder per project/environment |
| Ephemeral or one-off auth key | Forwarder gone or unable to rejoin after redeploy | Reusable, tagged key; ephemeral off; expiry disabled |
| No volume | New machine name on every redeploy | Mount the state volume |
| IP in env instead of name | Breaks when the node is re-created | Use the MagicDNS machine name |
| Proxy retired before cutover verified | Outage while debugging | §5 order; verify three times |
| ACL grant to `*` | Any tailnet node can reach a database | Tag-to-tag grants only; QUALITY sees widenings |
| Secret in receipt or message | Key or password in a JSON tail | Names only; rotate what leaked |
| Bot computer on the tailnet | Bot depends on a desktop being awake; holds a route to databases | Bots use the gateway over normal egress |
| `{{TAILSCALE_LEGACY_NODE_NAME}}` left as an unknown node | Untagged node with unknown reach | Identify in n1; adopt or retire under G-6 |

---

## Gate mapping

| Rule | Gate or directive | Why |
|---|---|---|
| Rollback plan with time and data implications in the receipt | G-5 | Env-by-file revert and proxy re-creation are the plan; "no data moved" is the data line |
| Proxy and domain removal need a recorded approval | G-6, PD-5 | Removing public access is destructive to anything still using it |
| No `TS_AUTHKEY`, passwords or bearer keys anywhere but the secret stores | G-3, PD-4 | Receipts, templates and messages are copied |
| Every probe recorded with its exit code; the unprobed goes in `unverified` | G-2, PD-1, PD-6 | "Reachable over the tailnet" is a claim a re-run must be able to check |
| `infra/railway/**`, `infra/tailscale/**` are INFRA's; ACL widenings go past QUALITY | G-1, PD-2 | Ownership is already declared for these paths |
</skill>
