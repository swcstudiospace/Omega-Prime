"""Vendored T3MP3ST declarative Grok Bot pack (red team capability family).

Provenance
    Upstream remote: https://github.com/swcstudiospace/purple-team.git
    Upstream commit: 1a1a4b012c2cf3a46d1899c9968ba440eb8bfda8
    License: MIT (see ``pack/LICENSE``)
    Copyright: 2026 SWC Studio Space
    Vendored: 2026-10-10

This package vendors the declarative pack only: markdown and JSON artifacts
under ``pack/`` (skills, seat templates, roster, plugin manifest, LICENSE).
No executable upstream code is vendored. The upstream ``bench/`` benchmark
corpora are excluded from this vendored copy for licensing reasons (they are
third-party fixtures with their own upstream licenses; see upstream
``THIRD-PARTY.md``).

Importing this module has no side effects beyond the standard library; it is
safe to import with no gateway present. The registry-side ``tempest_*`` tools
that drive the loopback gateway live in ``omega_prime.tools.redteam`` and are
registered only when the ``redteam`` family flag is enabled in config.
"""
