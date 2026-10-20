# Omega Prime Command Center

The Omega Prime Command Center is a local, read-only view of observed work. The operator starts it explicitly; it does not daemonize or start automatically. Its default page is the self-hosted React Command Center. The original static page remains at `/legacy` for recovery and compatibility.

## Start and stop

From the repository root:

```sh
python3 -m omega_prime.dashboard --host 127.0.0.1 --port 7421
```

The dashboard refuses `0.0.0.0`, `::`, and other non-loopback bind addresses. `--once` prints one snapshot as JSON; `--stop` stops the process recorded at `~/.omega-prime/dashboard/dashboard.pid`. `scripts/dashboard-start.sh` is an alternative explicit launcher. Open `http://127.0.0.1:7421/` on the same host. The module's default port can be set with `OMEGA_PRIME_DASHBOARD_PORT`.

Routes:

- `/` and `/cc/` serve the generated Command Center entry. `/cc` redirects to `/cc/` on the same host.
- `/legacy` serves the retained legacy dashboard. The built entry also has a persistent `/legacy` recovery link and a no-JavaScript message.
- `/api/snapshot`, `/api/stream`, and `/healthz` retain their existing contracts.
- The generated entry returns an explicit unavailable response with a `/legacy` link if the build is absent. Missing paths remain errors; the server does not serve the app entry as a catch-all.

The browser needs JavaScript for the Command Center. If the app fails to start, use `/legacy`. Static assets are generated during the frontend build and served by Python; Node is not a runtime requirement.

## Security model

The dashboard is loopback-only and read-only. Every non-GET method, including HEAD and OPTIONS, returns 405 before Host validation. Requests require an expected loopback Host and port. This protects against DNS rebinding and does not enable CORS. Static assets are limited to the built public entry, generated JavaScript/CSS/WOFF2 and license text under the Command Center asset namespace; filesystem containment checks reject traversal, hidden files and escaping symlinks. Legacy routes expose only their intended assets.

Responses carry a self-only Content Security Policy, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`. CSP restricts scripts, fonts, connections, images and default sources to `'self'`; `base-uri` is `'none'` and `frame-ancestors` is `'none'`. The only inline exception is `style-src 'unsafe-inline'` for motion styles. There are no external runtime origins, CDN scripts, data-URL fonts or inline executable scripts. Hashed build assets may be cached immutably; HTML revalidates and errors/license notices are not immutable.

Collector text is redacted at collector and publication boundaries and is rendered as escaped text. The React app does not use `innerHTML`/`dangerouslySetInnerHTML` for untrusted content. Navigation accepts safe HTTPS or genuine same-origin URLs; unsafe schemes, protocol-relative URLs and local filesystem paths are inert. GitHub credentials are resolved through the credential broker or supported environment variable, remain server-side, and are never sent to the browser. The dashboard does not read `/proc/*/environ`, full process arguments beyond the `--mode` value, `auth-broker.token`, pane history or screen contents. It offers no write actions, remote writes, reruns, merges or comments.

## API and live updates

`GET /api/snapshot` keeps exactly six top-level fields: `generated_at`, `sources`, `active`, `pr_gate`, `waiting_on_ove`, and `finished`. Task rows add nullable server-issued `seq` and `display_id` plus observed `timeline`; applicable rows carry structured `wiring`. Waiting rows can include `related_task_id`. The frontend consumes the existing named `snapshot` event from `/api/stream`; it does not introduce another API route or schema.

The browser fetches an initial snapshot, listens for the named `snapshot` event, and retains the last usable data during stream failure. It falls back to a five-second snapshot poll and retries the stream after 30 seconds. The server sends a `retry: 3000` instruction and a comment heartbeat every 15 seconds when unchanged. Source collection and browser transport health are distinct: a disconnected stream does not make cached source data disappear.

## Views and evidence

The four views are Overview, Tasks, CI/CD and Repositories. Each can distinguish first-load state, successful empty data, source error with retry, stale cached data and partial results. Healthy local work remains usable when GitHub is unavailable. Source status and freshness are evidence, not inferred success. The frontend's stale threshold for GitHub section data is 120 seconds; the source also reports its own cache metadata.

- **Overview** counts active, waiting-on-Ove, PR-gate and unique finished identities; shows source health, actual recent merges and observed activity. It does not fabricate state transitions from start/last-activity timestamps.
- **Tasks** supports search/filter, keyboard selection and detail without page reload. Detail shows available owner, source, state, model, times, progress, links and observed timeline. Unknown facts are labelled unavailable. A single technical identity remains selected and numbered across filters and active/finished views.
- **CI/CD** shows bounded PR/branch check evidence, branch/head SHA, status/conclusion, durations and accepted links. Greptile score is paired with its source and reviewed commit; absent or mismatched commit evidence stays unknown. Wiring associations require corroborated repository/branch/SHA evidence. A completed wiring row is not evidence that a PR merged or prerequisites passed; merge-order data is unavailable unless supplied.
- **Repositories** searches and sorts the cached `swcstudiospace` inventory. Null or unavailable metadata remains distinct from zero or a successful empty organization response.

### GitHub data and cache

The optional additive GitHub object is `sources.github.command_center`, with `repositories`, `ci_cd`, `recent_merges`, and `meta`; it does not add top-level snapshot fields. It is published as a source-status field after the collector's internal `SourceResult.extra` is projected. Repository rows include identity, visibility, default branch, push/language metadata, an enumerated open-PR count and default-branch checks when available. Counts enumerate open pull requests; `open_issues_count` is not used as a PR count. PR/check rows preserve repo, branch, base branch, head SHA, check status/conclusion/timestamps/duration/details URL and nullable Greptile evidence. Recent merges require an actual merged timestamp.

The GitHub collector uses `https://api.github.com` read-only GET requests, a 90-second cache, bounded pagination (at most three pages of 100 per paginated collection), capped projections, concurrency limits and request/deadline limits. It disables redirects and only follows validated API paths. Each subsection has its own fetch time and stale/partial/reason metadata. Cold no-token responses are empty/unavailable, not a successful fetch. No-token, offline, malformed response, HTTP 403 and HTTP 429 failures are distinguished; cached values and their original timestamps remain available when refresh fails. Truncation and incomplete counts are reported as such rather than shown as exact totals. The frontend does not receive credentials. The collector has no live GitHub side effects.

Other collectors refresh independently. `ok` means records were collected; `empty` means a successful collection found no records; `error` means collection/parsing failed; `unavailable` means a local dependency is absent; `disabled` means explicitly inactive. Existing sources include OMP sessions, wiring, sandbox files, omega jobs, command log, task JSON, AgentOS, processes, Herdr, Podman, GitHub and optional fleet.

## Durable task numbering

Task numbering is allocated by the server and persisted in `~/.omega-prime/dashboard/tasks.json`; the browser never assigns or derives display numbers. Each visible identity is formatted `NNNNN - Title`: at least five decimal digits, a space, hyphen, space, then the redacted title. Values greater than 99999 expand naturally; numbering is not truncated. The same technical identity retains its sequence across active/finished transitions, and sequences are never reused.

The v1 bridge keeps `tasks`, `waiting_on_ove` and `finished` arrays and adds the monotone `next_seq` allocator plus a SHA-256-keyed `identity_registry`. Registry records retain sequence tombstones, a redacted title, last state and observed `{at,state}` timeline. This preserves numbering when a row disappears or migrates. Existing bridge rows are numbered in their original `tasks` → `waiting_on_ove` → `finished` order; normalized collector-only identities are assigned deterministically after persisted rows. A restrictive shared sidecar lock spans validation and same-directory atomic replacement, preventing concurrent writers from reusing sequences. The existing writer remains `omega_prime.dashboard.status_file.write_tasks`.

`omega_prime.dashboard.task_ids` exposes `format_display_id`, `next_task_id`, `allocate_task_id`, `assign_task_ids` and `number_snapshot`:

- `next-id` prints a non-consuming next-number preview. It does not assign or change bridge bytes, mtime or counter.
- `assign-id --id <stable-namespaced-id> --title <title>` durably assigns or reuses one identity.
- `assign-ids` backfills existing bridge rows in their preserved order.

Use the existing task bridge writer for task updates. A preview is not proof of persisted assignment.

## Build, fonts and component attribution

The frontend lives in `omega_prime/dashboard/frontend/`. From that directory, the locked build workflow is:

```sh
npm ci --registry=https://registry.npmjs.org
npm test -- --run
npm run build
```

The production build runs TypeScript checking and Vite, emits hashed static assets into `omega_prime/dashboard/static/cc/`, and includes no source maps. The Python package includes the built entry, nested assets and notices. Inspect actual emitted filenames from the build; hashes are generated and are not stable documentation inputs. `CC_CHECK_BUILD=1 npm test -- --run src/test/build-assets.test.ts` validates built paths, local fonts/notices and measured output. Build-time npm is not needed when running the Python server.

Typeface selection is based on the supplied inspection record of Greptile's marketing CSS (2026-10-21), not a new live inspection in this change: Anybody for headings/counts, DM Sans for body copy, and Space Mono for identifiers/code/badges. The three Latin font families are self-hosted WOFF2 and distributed under SIL Open Font License 1.1; their actual notices ship in `omega_prime/dashboard/frontend/public/licenses/` and `omega_prime/dashboard/static/cc/licenses/`. The admitted direct dependency versions are React/React DOM 19.3.1, Motion 14.4.0, fontsource packages 5.3.0, TypeScript 7.0.2, Vite 8.3.4, Tailwind CSS and its Vite plugin 4.3.3, and Vitest 5.0.5; the exact resolved dependency graph is in `package-lock.json`.

Magic UI's Number Ticker, Blur Fade, Border Beam and Animated Shiny Text were adapted into the application; the upstream MIT notice and short in-app attribution are retained. Aceternity UI's Sidebar and Card Hover treatments are renamed, restyled and integrated as application components, not redistributed as upstream source; the supplied attribution/use note is shipped. See [Magic UI MIT notice](../omega_prime/dashboard/frontend/public/licenses/Magic-UI-MIT.txt), [Aceternity adaptation attribution](../omega_prime/dashboard/frontend/public/licenses/Aceternity-Attribution.txt), and the Anybody, DM Sans and Space Mono OFL notices in the same directory. No third-party source registry dump is committed.

Motion is decorative and never gates data visibility: counts and children render immediately. `prefers-reduced-motion` disables the decorative animation effects. The implementation includes focus and responsive styles, but automated DOM/build tests do not establish WCAG contrast or browser font rendering; visual AA and motion review require an actual browser inspection. The static assets' measured gzip size is recorded in the phase execution summary; it is a measurement, not a performance guarantee.

## Tasks bridge and settings

The writer produces a version-1 bridge with `tasks`, `waiting_on_ove` and `finished`; it uses restrictive directory/file permissions (0700/0600). Unknown keys are ignored. Missing or malformed data produces a source status, not a server crash. `~/.omega-prime/settings.json` may contain `dashboard` settings `window_hours` (default 24), `quiet_minutes` (default 15) and `fleet_enabled` (default false). Fleet is off unless explicitly enabled; it performs only its existing GET requests and keeps its token in memory only.

## Troubleshooting and evidence limits

- **Cannot bind:** use `127.0.0.1`; public and non-loopback addresses are refused.
- **Host rejected:** use the same loopback hostname and port as the server.
- **No task row:** inspect source status and confirm `tasks.json` validates; use `assign-id` for durable assignment, not `next-id`.
- **Herdr/Podman unavailable:** verify the local command or rootless socket; other sources continue independently.
- **GitHub stale/error:** check token configuration and the safe source status/reason; cached data may be retained.
- **Need a clean stop:** invoke `--stop`; it uses the dashboard pidfile under `~/.omega-prime/dashboard/`.

Local fixtures and fake GitHub transports prove offline contracts, not authenticated live parity. Phase 105's recorded local runtime smoke used a temporary home and loopback port, not production data or the existing 7421 service. Live PR/check/Greptile parity (a2), live organization inventory (a3), visual font/contrast/animation review (a4), signed sandbox receipt (a6), and four real-data screenshots (a7) remain unverified until the outer driver supplies their actual evidence. Do not infer them from tests or this documentation. The broad dashboard pytest selection has a recorded unrelated dependency-collection baseline; see `.planning/phases/105-command-center/105-05-SUMMARY.md` for the actual final command outcomes.
