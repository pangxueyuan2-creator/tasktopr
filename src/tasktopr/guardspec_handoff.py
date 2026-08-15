"""Optional fail-closed reader for a GuardSpec check JSON file.

TaskToPR never imports GuardSpec. When a decision file is present it must be
readable and explicitly allow the task; absence of the default file keeps
TaskToPR independent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .security import SecurityError

ALLOW_DECISIONS = frozenset({"allow", "allowed"})
DEFAULT_DECISION_NAME = ".guardspec-check.json"
ENV_DECISION_PATH = "GUARDSPEC_CHECK_JSON"


def resolve_decision_path(root: Path) -> Path | None:
    """Return the decision file path, or None when TaskToPR should stay independent."""

    raw = os.environ.get(ENV_DECISION_PATH)
    if raw is not None and raw.strip() != "":
        return Path(raw)
    default = root / DEFAULT_DECISION_NAME
    if default.is_file():
        return default
    return None


def load_guardspec_decision(root: Path) -> dict[str, Any] | None:
    """Load the optional GuardSpec decision object.

    Missing default file → None (independent).
    Env path set but missing, or unreadable JSON → SecurityError (fail-closed).
    """

    path = resolve_decision_path(root)
    if path is None:
        return None
    if not path.is_file():
        raise SecurityError(f"GuardSpec decision file is required but missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecurityError(f"GuardSpec decision file is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise SecurityError("GuardSpec decision file must be a JSON object.")
    return payload


def enforce_guardspec_decision(root: Path) -> dict[str, Any] | None:
    """Refuse to mutate the tree unless a present decision explicitly allows it."""

    payload = load_guardspec_decision(root)
    if payload is None:
        return None
    decision = payload.get("decision")
    if not isinstance(decision, str) or not decision.strip():
        raise SecurityError("GuardSpec decision file is missing a decision.")
    digest = payload.get("policy_digest")
    digest_note = f" policy_digest={digest}" if digest else ""
    if decision.casefold() not in ALLOW_DECISIONS:
        raise SecurityError(
            f"GuardSpec decision is {decision}; refusing to apply patch.{digest_note}"
        )
    return payload
