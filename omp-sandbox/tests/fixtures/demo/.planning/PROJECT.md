# Project: greet (disposable omp-sandbox e2e fixture)

## What This Is
A throwaway one-file shell CLI used only to prove that omp + GSD-Core can plan and execute inside the sandbox.

## Core Value
`./greet.sh NAME` prints a greeting.

## Constraints
- POSIX sh only, no dependencies, no network, no package installs.
- Local git only. Never push, never open a PR.

## Key Decisions
| Decision | Rationale | Outcome |
|---|---|---|
| Single file `greet.sh` | Smallest real task | — Pending |
