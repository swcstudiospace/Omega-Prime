# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Spectrum Web Co
"""Sandbox control for Omega Prime (00045).

Drives OpenShell sandboxes on the Podman driver: create, exec, down, status
and list, plus the Herdr pane command that runs the interactive omp agent
inside a sandbox. Every subprocess goes through an injected runner, so tests
never touch OpenShell or Podman. The provider never starts the gateway, the
Podman socket or an image build; those stay with the operator and the
omp-sandbox tier.
"""

from omega_prime.sandbox_control.provider import (
    OWNED_PREFIX,
    OpenShellSandboxProvider,
    RunResult,
    SandboxConfig,
    SandboxControlError,
)

__all__ = [
    "OWNED_PREFIX",
    "OpenShellSandboxProvider",
    "RunResult",
    "SandboxConfig",
    "SandboxControlError",
]
