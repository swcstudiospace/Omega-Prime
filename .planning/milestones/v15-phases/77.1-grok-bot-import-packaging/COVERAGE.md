# API Coverage — Phase 77.1: Grok Bot import packaging

> Full coverage by default. Opt-outs are explicit, reasoned decisions.

No external API integration: this phase ships declarative Cursor Plugin manifests and a stdio launcher command consumed by Grok Bot and Cursor team marketplaces, plus a deterministic local generator; it makes no external API or SDK calls and adds no dependency.

Detector receipt: `api-coverage.cjs --json` over all eight 77.1 PLAN bodies plus the ROADMAP 77.1 section returned `{"detected":false}` (exit 1, real negative, not a skip) on 2026-10-12. The platform surface this phase packages for (Cursor Plugin component surface: marketplace manifest, plugin manifest, `skills`, `commands`, `mcpServers` stdio, `mcpServers` remote HTTPS, `rules`, `agents`, `hooks`, plugin `variables`, logo/metadata; Grok Bot custom MCP server types: Command/stdio, Remote HTTPS) is a set of declarative files read by the platforms, not an API this phase calls — so there is no capability surface to INTEGRATE or OPT-OUT of. The decided record for that surface lives in the plans themselves: `mcp.json` declares only the stdio Command default (77.1-05), the HTTPS entry ships as docs plus a non-referenced example (77.1-05, 77.1-07), and `rules`/`agents`/`hooks`/`variables`/logo are not shipped (no plan emits them).
