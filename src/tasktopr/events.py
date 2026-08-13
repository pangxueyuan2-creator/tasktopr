"""Run artifacts and structured event logging for transparent agent execution."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from .models import RunPhase
from .security import redact


class RunJournal:
    """Create and write the evidence bundle for a single TaskToPR run."""

    def __init__(self, repo_root: Path, console: Console | None = None) -> None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = repo_root / ".tasktopr" / "runs" / f"{timestamp}-{secrets.token_hex(3)}"
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self._events_path = self.run_dir / "events.jsonl"
        self.console = console or Console()

    def event(
        self,
        phase: RunPhase,
        message: str,
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        """Append a redacted event and render the same status to the terminal."""

        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "phase": phase.value,
            "level": level,
            "message": redact(message),
            "data": self._redacted_data(data or {}),
        }
        with self._events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        style = {"error": "bold red", "warning": "yellow", "info": "cyan"}.get(level, "white")
        self.console.print(f"[{style}]{phase.value}[/] {payload['message']}")

    def write_json(self, name: str, payload: Any) -> Path:
        """Persist a redacted JSON artifact under the run directory."""

        path = self.run_dir / name
        path.write_text(
            json.dumps(self._redacted_data(payload), ensure_ascii=False, indent=2, default=str)
            + "\n",
            encoding="utf-8",
        )
        return path

    def write_markdown(self, name: str, content: str) -> Path:
        """Persist a redacted Markdown artifact under the run directory."""

        path = self.run_dir / name
        path.write_text(redact(content), encoding="utf-8")
        return path

    def _redacted_data(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): self._redacted_data(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self._redacted_data(item) for item in value]
        if hasattr(value, "model_dump"):
            return self._redacted_data(value.model_dump(mode="json"))
        return value
