---
name: android
description: Writing and reviewing Android code — Kotlin, Compose, Gradle, release. Use for any change to .kt files, Gradle build files, or Play Store release.
bots: [bot-00-omega-prime]
gates: [G-5, G-6]
---
<skill name="android" version="1">
# Android

## L1 — Summary

**Once a release is out you cannot recall it.** You can halt a rollout and ship a fix, but users
already on the bad build stay there until they choose to update — often weeks.

That asymmetry drives everything: staged rollout, real device verification, and honest
`unverified` entries about the devices you did not have.

**Decision tree:**

```
Touching UI state?
├─▶ §1 Compose state hoisting. Then §3 — will it survive process death?
│
Doing background or async work?
├─▶ §2. Lifecycle-scoped coroutines. Never GlobalScope.
│
Using an API added after minSdk?
├─▶ Guard it. §4. An unguarded call crashes on older devices.
│
Releasing?
└─▶ §6. Staged rollout, approval recorded, halt criteria named.
```

**Verification:** `testDebugUnitTest`, `lintDebug`, `assembleDebug`, **and run it on a device at
minSdk**. A release build additionally needs R8 verification — R8 failures appear only in release.

---

## L2 — Method

### §1 Compose

- **Hoist state.** Composables take state and emit events; they do not own state that outlives
  them. A composable owning shared state is untestable and re-renders unpredictably.
- `remember` for composition-scoped values; `rememberSaveable` for anything that must survive
  configuration change.
- Unidirectional data flow: state down, events up.
- Stability matters for recomposition. Unstable parameters — a raw `List`, a lambda recreated each
  composition — cause recomposition storms. Use `ImmutableList` or `@Stable`, and hoist lambdas.
- Never perform side effects directly in a composable body. `LaunchedEffect`, `SideEffect`,
  `DisposableEffect` — a composable body can run many times per frame.
- Preview annotations for anything visual, so you can actually look at it (G-2).

### §2 Coroutines

- Scope to a lifecycle: `viewModelScope`, `lifecycleScope`, `repeatOnLifecycle`. **Never
  `GlobalScope`** — it outlives the screen and leaks.
- `repeatOnLifecycle(STARTED)` for collecting flows in UI. Collecting in `onCreate` keeps
  collecting while the app is backgrounded, burning battery and sometimes crashing on UI updates.
- Dispatchers: `Main` for UI, `IO` for network and disk, `Default` for CPU. Inject them so tests
  can substitute.
- Structured concurrency: a failing child cancels siblings. That is usually what you want;
  `supervisorScope` where it is not.
- Long-running work that must survive the process belongs in WorkManager, not a coroutine.

### §3 Lifecycle and process death

**Most Android bugs are lifecycle bugs, and process death is the one that reaches production.** It
is rare in development and routine on a real device under memory pressure.

| Event | Test it by |
|---|---|
| Configuration change | Rotate the device |
| Process death | Developer options → "Don't keep activities", or `adb shell am kill <pkg>` |
| Background restriction | Background the app, wait, return |
| Permission revoked while backgrounded | Revoke in settings while backgrounded |
| Deep link into cold start | `adb shell am start -W -a VIEW -d "<uri>"` |

State that must survive process death goes in `SavedStateHandle` or `rememberSaveable`. A
ViewModel does **not** survive process death — this is the single most common misconception, and
it produces a crash-on-restore that is very hard to reproduce after the fact.

### §4 API levels and permissions

```kotlin
if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
    // API 33+ only
} else {
    // fallback for the supported range
}
```

Lint catches most unguarded usage — do not add baseline entries to suppress it (PD-3).

**Permissions:** request in context, not at launch. Handle denial and permanent denial as normal
states with a working degraded experience. `POST_NOTIFICATIONS` is runtime-requested from API 33.

**Raising `minSdk` drops users.** It is a product decision with a revenue consequence, not a build
tidy-up. Escalate with current install-base numbers.

### §5 Gradle

- Version catalogs (`libs.versions.toml`) over hardcoded versions.
- Kotlin DSL (`.kts`) for new build files.
- Check the build file before assuming a dependency exists.
- Compare APK/AAB size before and after adding a dependency (G-2). Mobile bundle size affects
  install conversion measurably.
- Do not add a plugin to solve something the existing build already does.

### §6 Release

**Never a full rollout in one step.**

```
internal testing → closed testing → staged production (start small) → widen gradually
```

Between stages, watch crash-free rate, ANR rate, and the reviews. Name the halt threshold and the
owner watching it in the receipt — "we'll keep an eye on it" is not monitoring.

Production release requires recorded human approval (G-6) with:

- The staged rollout percentage
- The halt criteria (an actual number)
- Who is watching, and for how long
- The halt procedure

**R8/ProGuard:** release builds shrink and obfuscate, which breaks reflection- and
serialisation-dependent code. This fails **only in release**, so a green debug build proves
nothing about it. Test the release build before shipping.

When R8 breaks something, read the stack trace and add a **narrow** keep rule for the specific
class. A blanket app-wide keep rule disables optimisation everywhere to avoid understanding one
failure — it inflates the binary and hides the real problem.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| ViewModel assumed to survive process death | Crash on restore, unreproducible | `SavedStateHandle` |
| `GlobalScope` | Leaks, work continuing after screen gone | Lifecycle-scoped |
| Collecting flow in `onCreate` | Battery drain, background crashes | `repeatOnLifecycle` |
| Unstable Compose params | Jank, recomposition storms | Immutable types, hoisted lambdas |
| Unguarded new API | Crash on older devices | `Build.VERSION.SDK_INT` guard |
| Lint baseline to hide findings | Real issues suppressed | Fix them (PD-3) |
| Blanket ProGuard keep | Bloated APK, root cause hidden | Narrow rule for the named class |
| Debug-only testing | Release-only crash reaches users | Test the release build |
| Full rollout | No way to limit the blast radius | Staged, always |
</skill>
