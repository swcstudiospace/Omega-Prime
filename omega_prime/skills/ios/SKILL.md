---
name: ios
description: Writing and reviewing iOS code — Swift, SwiftUI, Xcode, App Review. Use for any change to .swift files, the Xcode project, or App Store release.
bots: [bot-00-omega-prime]
gates: [G-5, G-6]
---
<skill name="ios" version="1">
# iOS

## L1 — Summary

**iOS has the least forgiving release model in this system.** Review takes days, expedited review
is a finite favour, and a phased release can be halted but not reversed. Users on a bad build stay
there.

Two things follow: TestFlight before production, always; and anything touching permissions,
entitlements, purchases or account deletion gets flagged **before** submission, because a
rejection costs days and the fix usually took ten minutes to get right up front.

**Decision tree:**

```
Touching permissions, entitlements, purchases, or account deletion?
├─▶ STOP. §3 App Review risk. Flag before implementing.
│
Writing concurrent code?
├─▶ §2. Do not suppress an isolation warning to clear the build.
│
Touching UI state?
├─▶ §1. @State local, @Binding passed, @Observable shared.
│
Releasing?
└─▶ §4. TestFlight → phased release → approval recorded.
```

**Verification:** build Debug **and Release**, tests, run on the **minimum** supported iOS
version, and look at the UI. No new warnings — Swift warnings are frequently real bugs.

---

## L2 — Method

### §1 SwiftUI

**State ownership, chosen by who owns the truth:**

| Property wrapper | Use when |
|---|---|
| `@State` | This view owns it, nothing outside needs it |
| `@Binding` | A parent owns it, this view mutates it |
| `@Observable` (or `@StateObject`) | Shared model, view observes |
| `@Environment` | Ambient dependency passed implicitly |

Getting this wrong produces views that do not update, or update constantly. Both symptoms point
back to ownership.

- Keep view bodies cheap. They run frequently; expensive work belongs in the model.
- Extract subviews for reuse **and** for recomposition scope.
- `@ViewBuilder` for conditional content.
- Previews for anything visual, so you can look at it (G-2).
- Prefer the built-in components — they get accessibility, Dynamic Type and platform behaviour
  free, and reimplementing them means reimplementing all three badly.

### §2 Swift concurrency

The compiler checks this, and the checking is getting stricter each release.

- `@MainActor` for anything touching UI.
- `actor` for shared mutable state — it serialises access by construction.
- Structured concurrency: `async let`, `TaskGroup`. A `Task {}` detached from a lifecycle leaks.
- `Sendable` conformance for anything crossing an isolation boundary.

**Do not suppress isolation warnings to clear a build.**

`@unchecked Sendable` and `nonisolated(unsafe)` assert a thread-safety property the compiler could
not verify. If you cannot state why the invariant holds, you do not know that it does — and
concurrency bugs surface as rare, unreproducible corruption rather than clean crashes, which makes
them among the most expensive bugs to chase.

```swift
// acceptable — the guarantee is stated and real
/// Access is serialised by `queue`; no other path touches `storage`.
final class Cache: @unchecked Sendable {
    private let queue = DispatchQueue(label: "cache")
    private var storage: [String: Data] = [:]
}

// better — the compiler enforces it instead of trusting you
actor Cache {
    private var storage: [String: Data] = [:]
}
```

### §3 App Review

A human process with published guidelines and consistent failure patterns.

| Risk | What triggers it |
|---|---|
| Permissions | Requesting without clear in-context justification; usage string not matching actual use |
| Private API | Any use, including via a dependency you did not audit |
| Payments | Digital goods routed outside StoreKit |
| Account deletion | Required in-app where accounts can be created |
| Broken functionality | Anything unreachable, placeholder, or failing on the reviewer's device |
| Sign in with Apple | Required where other third-party sign-in is offered |
| Data collection | Privacy nutrition labels not matching actual behaviour |

**Usage descriptions:** write what the app actually does with the data. The honest description is
also the one that passes review, and a vague one ("to improve your experience") is a common
rejection.

**Flag before submission**, not after rejection. Permissions, entitlements, purchase flows and
account management all get called out in the receipt.

### §4 Release

```
local → TestFlight internal → TestFlight external → phased release → monitor
```

Phased release rolls out over days and **can be paused but not reversed**. Users already updated
stay updated.

Production submission requires recorded approval (G-6) with the halt criteria, who is watching,
and the fix-forward plan. Since there is no rollback, the fix-forward plan is the actual plan.

**Build a real archive and validate it** before submitting. Release builds differ from debug —
optimisation, stripped symbols, different entitlements — and the differences produce release-only
failures.

### §5 Xcode project

- Prefer Swift Package Manager over CocoaPods for new dependencies.
- Project file conflicts are painful; coordinate rather than merging them blind.
- Build settings belong in `.xcconfig` files, where they are reviewable in a diff rather than
  buried in project XML.
- Check `Package.swift` before assuming a dependency exists.
- Keep schemes in source control and shared, so CI and every developer build the same thing.

### §6 Testing

- Unit tests for logic; UI tests for critical flows only — UI tests are slow and flaky enough that
  a large suite gets ignored.
- `XCTestExpectation` or async test functions for asynchronous code.
- Snapshot tests for visual regression, reviewed when they change rather than blindly regenerated.
- **Test on the minimum supported iOS version.** The newest simulator is not evidence about the
  oldest supported OS, and that gap is where availability bugs live.

---

## Failure modes

| Mode | Symptom | Fix |
|---|---|---|
| `@unchecked Sendable` to clear a build | Rare data corruption in production | Use an actor, or state the real guarantee |
| Wrong state wrapper | View does not update, or updates constantly | §1 — who owns the truth |
| Detached `Task {}` | Work continues after the view is gone | Structured concurrency, lifecycle-scoped |
| Permission at launch | Review rejection, high denial rate | Request in context |
| Vague usage description | Review rejection | Describe actual use |
| Newest-simulator-only testing | Crash on the minimum supported OS | Test at minimum |
| Debug-only verification | Release-only failure ships | Build and test Release |
| Warnings ignored | Real bugs hidden in the noise | Zero new warnings |
</skill>
