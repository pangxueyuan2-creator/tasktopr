"""Run artifacts and structured event logging for transparent agent execution."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from .models import RunPhase
from .security import SecurityError, redact


class RunJournal:
    """Create and write the evidence bundle for a single TaskToPR run."""

    def __init__(self, repo_root: Path, console: Console | None = None) -> None:
        self.repo_root = repo_root.resolve()
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = repo_root / ".tasktopr" / "runs" / f"{timestamp}-{secrets.token_hex(3)}"
        self._check_path(self.run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self._events_path = self.run_dir / "events.jsonl"
        self.console = console or Console()

    def _check_path(self, path: Path) -> None:
        current = path
        while current != self.repo_root:
            if not current.is_relative_to(self.repo_root):
                raise SecurityError("Journal path escaped repository.")
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise SecurityError("Journal paths cannot be symlinks or junctions.")
            current = current.parent

    def _write(self, name: str, content: str) -> Path:
        if Path(name).name != name or name in {"", ".", ".."} or ":" in name or "\\" in name:
            raise SecurityError("Journal artifact name must be a plain filename.")
        path = self.run_dir / name
        self._check_path(path)
        descriptor, temporary = tempfile.mkstemp(prefix=".journal-", dir=self.run_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self._check_path(path)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return path

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
        self._check_path(self._events_path)
        with self._events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        style = {"error": "bold red", "warning": "yellow", "info": "cyan"}.get(level, "white")
        self.console.print(f"[{style}]{phase.value}[/] {payload['message']}")

    def write_json(self, name: str, payload: Any) -> Path:
        """Persist a redacted JSON artifact under the run directory."""

        return self._write(
            name,
            json.dumps(self._redacted_data(payload), ensure_ascii=False, indent=2, default=str)
            + "\n",
        )

    def write_markdown(self, name: str, content: str) -> Path:
        """Persist a redacted Markdown artifact under the run directory."""

        return self._write(name, redact(content))

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
