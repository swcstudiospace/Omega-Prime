"""Runner agent inside the Railway Desktop (data plane, no GitHub credential).

``supervisor`` owns child-process lifecycle; ``app`` is the plain-ASGI surface
the controller drives. Both are stdlib-only; serving uses the lazy uvicorn
extra through ``python -m omega_prime.ci_runners agent``.
"""

from __future__ import annotations
