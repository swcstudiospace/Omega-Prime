# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Build an additive, run-owned project skills view for the installed omp loader.

Fixture consumers can verify the attachment mechanics here; only the real-loader
integration test proves that the installed omp resolves these manifests.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from omega_prime.commands.generated import checked_in_skills
from omega_prime.engine.mcp_bridge import AcquiredAttachment, install_root


def _remove_if_identity(
    attachment: AcquiredAttachment, relative_path: str, identity: tuple[int, int]
) -> None:
    with contextlib.suppress(FileNotFoundError):
        attachment.remove_artifact(relative_path, identity)


def attach_skills(work_root: Path, attachment: AcquiredAttachment) -> dict[str, Any]:
    """Attach all catalog skills using the driver's already-acquired lease.

    The configured GSD custom directory is natively discovered by omp, so this
    project view contains only Omega catalog entries and never copies GSD files.
    On any conflict or I/O failure, only identities created by this call may be
    removed and the caller can continue without a skills view.
    """
    work = Path(work_root)
    if Path(os.path.abspath(work)) != attachment.work_root or not attachment.active:
        return {
            "status": "attach_failed",
            "view": "",
            "reason": "attachment_lease_unavailable",
            "linked": [],
            "sources": {
                "omega": {"status": "attach_failed", "root": "", "count": 0},
                "gsd": {"status": "not_linked_native", "root": "", "names": []},
            },
        }
    view = work / ".omp" / "skills"
    catalog_root = install_root()
    omega_root = catalog_root / "omega_prime" / "skills"
    linked: list[dict[str, str]] = []
    created: list[tuple[str, tuple[int, int]]] = []

    def failed(reason: str) -> dict[str, Any]:
        for relative_path, identity in reversed(created):
            with contextlib.suppress(OSError, ValueError):
                _remove_if_identity(attachment, relative_path, identity)
        return {
            "status": "attach_failed",
            "view": "",
            "reason": reason,
            "linked": [],
            "sources": {
                "omega": {
                    "status": "attach_failed",
                    "root": str(omega_root),
                    "count": 0,
                },
                "gsd": {"status": "not_linked_native", "root": "", "names": []},
            },
        }

    try:
        names = checked_in_skills(catalog_root)
        try:
            view_identity = attachment.create_directory_artifact(".omp/skills")
        except FileExistsError:
            return failed(
                "skills_view_is_symlink"
                if view.is_symlink()
                else "skills_view_preexisting"
            )
        created.append((".omp/skills", view_identity))

        for name in names:
            source = omega_root / name
            if not (source / "SKILL.md").is_file():
                raise FileNotFoundError(
                    f"missing skill manifest: {source / 'SKILL.md'}"
                )
            relative_path = f".omp/skills/{name}"
            link_identity = attachment.create_symlink_artifact(
                relative_path, source.resolve()
            )
            created.append((relative_path, link_identity))
            linked.append({"name": name, "source": str(source.resolve())})
    except Exception as exc:
        return failed(f"{type(exc).__name__}:{exc}")

    return {
        "status": "attached",
        "view": str(view),
        "reason": "",
        "linked": linked,
        "sources": {
            "omega": {
                "status": "attached",
                "root": str(omega_root),
                "count": len(linked),
            },
            "gsd": {"status": "not_linked_native", "root": "", "names": []},
        },
    }
