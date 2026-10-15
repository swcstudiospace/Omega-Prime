# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""External service clients used by the desk tool packs.

Per-install Linear, Notion, and optional gateway bindings live beside those
clients. Secrets stay in the host environment or a mode 0600 file. The
binding file records only identifiers and states.
"""
