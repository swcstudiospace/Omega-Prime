---
name: deno-typescript
description: Writing and reviewing TypeScript and Deno code. Use for any change to .ts/.tsx files, deno.json, or package.json.
bots: [bot-00-omega-prime]
---
<skill name="deno-typescript" version="1">
# TypeScript & Deno

## L1 — Summary

TypeScript's safety is opt-in and easy to disable by accident. **`any` and a non-null assertion
are both ways of telling the compiler to stop helping** — used casually, you get the syntax of a
typed language with none of the guarantees.

Deno's permission model is the other half: it is a real security boundary, and `-A` gives it away.

**Decision tree:**

```
Type error you cannot resolve?
├─▶ Do not reach for `any` or `!`. §1. The error usually describes a real
│   case your code does not handle.
│
Running Deno?
├─▶ Narrowest permissions that work. §3. Never blanket -A.
│
Handling external data — API, form, storage?
├─▶ Parse and validate at the boundary. §2. A type assertion on untrusted
│   input is a lie the compiler believes.
│
Deploying?
└─▶ skills/vercel/SKILL.md
```

**Verification:** `deno check` / `tsc --noEmit`, `deno lint` / `eslint`, tests, **and a build**.
Type-checking is not building.

---

## L2 — Method

### §1 Types

```ts
// disabling the type system
function handle(data: any) { return data.value.nested }   // no checking at all
const user = maybeUser!                                    // asserting non-null on faith
const cfg = json as Config                                 // asserting shape on faith

// keeping it
function handle(data: unknown) {
  if (!isPayload(data)) throw new TypeError("unexpected payload")
  return data.value.nested          // narrowed, checked
}
```

- `unknown` over `any` — it forces you to narrow before use.
- Non-null `!` only where you have just checked. Otherwise handle the null.
- `as` is an assertion, not a conversion. On external data it is a lie the compiler accepts.
- Discriminated unions for state. Make invalid states unrepresentable:

```ts
type Result<T> =
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "ready"; data: T }
// beats { loading: boolean; error?: Error; data?: T } which permits loading+error+data
```

- `satisfies` to check a literal against a type without widening it.
- Branded types for identifiers: `type UserId = string & { readonly __brand: "UserId" }`.

### §2 Boundaries

Everything crossing into your code is `unknown` until validated: API responses, form input,
`localStorage`, URL params, environment variables, webhook bodies.

```ts
import { z } from "zod"

const Account = z.object({
  id: z.string().uuid(),
  email: z.string().email(),
  createdAt: z.coerce.date(),
})
type Account = z.infer<typeof Account>

const account = Account.parse(await res.json())   // throws on unexpected shape
```

The contract in `contracts/` is the source of truth for API shapes. Generate types from it where
possible — a hand-maintained duplicate drifts, and the drift is discovered by a user.

### §3 Deno permissions

Grant the narrowest set that works. The permission model is the main security property Deno
offers; `-A` discards it.

```bash
deno run -A app.ts                                              # full access
deno run --allow-net=api.example.com --allow-env=API_KEY app.ts # scoped
```

- `--allow-net` with specific hosts, not bare.
- `--allow-env` with specific variable names.
- `--allow-read` / `--allow-write` with specific paths.
- Never `--allow-run` without a very good reason; it is arbitrary code execution.

Record the permission set in `deno.json` tasks so it is reviewable rather than living in someone's
shell history.

### §4 Modules

- **Deno:** import maps in `deno.json`. Pin versions. Commit `deno.lock` — an unpinned remote
  import is a supply-chain exposure and a reproducibility problem at once.
- **Node/Vercel:** `package.json`, lockfile committed.
- Do not mix Deno-style URL imports and npm specifiers in the same module without a reason.
- Check the manifest before importing. Assuming a library is present is the most common
  self-inflicted build break.

### §5 Async

- `async`/`await` over raw promise chains.
- `Promise.all` for independent work; `Promise.allSettled` when partial failure is acceptable.
  `Promise.all` rejects on the first failure and abandons the rest, which is sometimes right and
  often not.
- Always handle rejection. An unhandled rejection terminates a Deno process.
- `AbortController` for anything cancellable — especially fetches tied to a component lifetime.
- Never `await` inside a loop over independent items; collect promises and await once.

### §6 Testing

- Deno: `Deno.test` with `@std/assert`. Node: vitest or node:test.
- Test behaviour, not implementation.
- For components: render and assert on what a user sees, via Testing Library. Snapshot tests that
  nobody reads are noise — they get regenerated on failure without inspection.
- Mock at the network boundary (MSW or equivalent), not your own modules.

### §7 Frontend specifics

- Handle loading, empty, error and offline states. The happy path is a quarter of the work.
- Keys in lists must be stable identifiers, never array indices — index keys cause state to attach
  to the wrong row on reorder.
- Effects need cleanup. A fetch in an effect without an `AbortController` sets state after unmount.
- Semantic HTML first; ARIA only where semantics fall short.
- Never put a secret in a client-visible variable. Anything in the bundle is public.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| `any` spreading | Type errors reappear at runtime | `unknown` + narrowing |
| `as` on external data | Runtime shape mismatch | Parse with a schema at the boundary |
| `-A` everywhere | No security boundary left | Scope each permission |
| Unpinned remote imports | Non-reproducible builds, supply-chain risk | Pin and lock |
| Type-check as "build" | Build fails after a green check | Run the build |
| Index keys | Wrong row state after reorder | Stable ids |
| Missing effect cleanup | setState-after-unmount warnings | AbortController, cleanup return |
| `Promise.all` for partial | One failure loses all results | `allSettled` where partial is fine |
</skill>
