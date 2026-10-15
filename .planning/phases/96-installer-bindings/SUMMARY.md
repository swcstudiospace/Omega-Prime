# Phase 96 - Summary

**Result:** A fresh install walks Notion template, Notion connect, schema check, Linear team and project, then an optional gateway. The binding file is outside the repo and holds no tokens.

**Contract version:** 1. Required Notion properties are Name (title) and Status (Not started, Up next, In progress, Done). Due date and Project are optional.

**Deferral:** Extra submodules were not added. Each omitted checkout is named in docs/setup.md. The legacy Linear path remains only when LINEAR_API_KEY is unset. Audit: .planning/milestones/v19-MILESTONE-AUDIT.md.

**Verification:** ruff, ruff format, and mypy were clean (441 files). Pytest was 4679 passed and 3 skipped. Evals 26/26. Catalog, plugin, prompt format, assemble, provenance, and readiness checks passed.

**Secret check:** Example docs name `NOTION_TOKEN` and `LINEAR_API_KEY` with no values. The installer test refuses a pasted sentinel and does not write it.


[You have received this identical output 3 times. Re-reading '/root/src/repos/omega-prime/.planning/phases/96-installer-bindings/SUMMARY.md:raw' will not change it — use a narrower selector (path:A-B), or proceed with the edit.]