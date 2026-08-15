"""Human-facing command-line interface for TaskToPR."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from .agents import git_root, list_changed_files, review_changes
from .config import (
    ConfigError,
    TaskToPRConfig,
    apply_boundary,
    load_boundary,
    load_config,
    provider_api_key,
    redacted_config,
)
from .orchestrator import fix_issue, plan_issue
from .providers import DemoProvider, ModelProvider, ProviderError, build_provider

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Turn a GitHub Issue into a transparent, tested Pull Request.",
)
console = Console()


def _config_and_provider(
    start_dir: Path,
    provider_name: str | None,
    model: str | None,
    demo: bool,
    boundary: Path | None = None,
) -> tuple[Path, TaskToPRConfig, ModelProvider]:
    root = git_root(start_dir)
    config = load_config(root)
    if boundary is not None:
        apply_boundary(config, load_boundary(boundary if boundary.is_absolute() else root / boundary))
    if provider_name:
        config.agent.provider = provider_name
    if model:
        config.agent.model = model
    provider = DemoProvider() if demo else build_provider(config.agent.provider)
    return root, config, provider


def _render_result(result_message: str, run_dir: Path, success: bool) -> None:
    style = "bold green" if success else "bold red"
    console.print(f"[{style}]{result_message}[/]")
    console.print(f"Evidence: [cyan]{run_dir}[/]")


@app.command()
def plan(
    issue_number: Annotated[int, typer.Argument(min=1, help="GitHub Issue number.")],
    provider: Annotated[
        str | None, typer.Option("--provider", help="openai, anthropic, or openai-compatible.")
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", help="Override configured model name.")
    ] = None,
    demo: Annotated[
        bool, typer.Option("--demo", help="Use the local deterministic demo Issue/provider.")
    ] = False,
    boundary: Annotated[
        Path | None,
        typer.Option(
            "--boundary",
            help="Independent agent-boundary/v1 JSON file. Issue text is never treated as policy.",
        ),
    ] = None,
) -> None:
    """Read an Issue and print a validated plan without modifying the repository."""

    try:
        root, config, model_provider = _config_and_provider(
            Path.cwd(), provider, model, demo, boundary
        )
        result = plan_issue(
            issue_number, start_dir=root, config=config, provider=model_provider, demo=demo
        )
    except (ConfigError, ProviderError, RuntimeError) as exc:
        console.print(f"[bold red]Configuration error:[/] {exc}")
        raise typer.Exit(2) from exc
    _render_result(result.message, result.run_dir, result.success)
    if result.plan:
        console.print_json(result.plan.model_dump_json(indent=2))
    if not result.success:
        raise typer.Exit(1)


@app.command()
def fix(
    issue_number: Annotated[int, typer.Argument(min=1, help="GitHub Issue number.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Create only a plan and evidence bundle.")
    ] = False,
    no_pr: Annotated[
        bool,
        typer.Option(
            "--no-pr", help="Keep reviewed edits local; do not commit, push or create a PR."
        ),
    ] = False,
    provider: Annotated[
        str | None, typer.Option("--provider", help="openai, anthropic, or openai-compatible.")
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", help="Override configured model name.")
    ] = None,
    demo: Annotated[
        bool, typer.Option("--demo", help="Use the deterministic local demo provider and Issue.")
    ] = False,
    boundary: Annotated[
        Path | None,
        typer.Option(
            "--boundary",
            help="Independent agent-boundary/v1 JSON file. Issue text is never treated as policy.",
        ),
    ] = None,
) -> None:
    """Plan, patch, test, review and optionally create a Pull Request for one Issue."""

    try:
        root, config, model_provider = _config_and_provider(
            Path.cwd(), provider, model, demo, boundary
        )
        result = fix_issue(
            issue_number,
            start_dir=root,
            config=config,
            provider=model_provider,
            dry_run=dry_run,
            no_pr=no_pr,
            demo=demo,
        )
    except (ConfigError, ProviderError, RuntimeError) as exc:
        console.print(f"[bold red]Configuration error:[/] {exc}")
        raise typer.Exit(2) from exc
    _render_result(result.message, result.run_dir, result.success)
    if result.pr_url:
        console.print(f"Pull Request: [link={result.pr_url}]{result.pr_url}[/link]")
    if not result.success:
        raise typer.Exit(1)


@app.command()
def review(
    boundary: Annotated[
        Path | None,
        typer.Option(
            "--boundary",
            help="Independent agent-boundary/v1 JSON file. Issue text is never treated as policy.",
        ),
    ] = None,
) -> None:
    """Review current working-tree changes against scope, safety and test evidence gates."""

    try:
        root = git_root(Path.cwd())
        config = load_config(root)
        if boundary is not None:
            apply_boundary(
                config,
                load_boundary(boundary if boundary.is_absolute() else root / boundary),
            )
        changed = list_changed_files(root)
        result = review_changes(root, changed, [], config)
    except (ConfigError, RuntimeError) as exc:
        console.print(f"[bold red]Review unavailable:[/] {exc}")
        raise typer.Exit(2) from exc
    console.print_json(result.model_dump_json(indent=2))
    if not result.approved:
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show the most recent evidence bundle in the current repository."""

    try:
        root = git_root(Path.cwd())
    except RuntimeError as exc:
        console.print(f"[bold red]Status unavailable:[/] {exc}")
        raise typer.Exit(2) from exc
    runs = (
        sorted((root / ".tasktopr" / "runs").glob("*"))
        if (root / ".tasktopr" / "runs").exists()
        else []
    )
    if not runs:
        console.print("No TaskToPR runs found in this repository.")
        return
    latest = runs[-1]
    console.print(f"Latest run: [cyan]{latest.name}[/]")
    summary = latest / "summary.md"
    if summary.exists():
        console.print(summary.read_text(encoding="utf-8", errors="replace"))


@app.command()
def doctor() -> None:
    """Check the local repository, GitHub CLI, runtimes and configured model provider."""

    table = Table(title="TaskToPR doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")
    try:
        root = git_root(Path.cwd())
        config = load_config(root)
        table.add_row("Git repository", "OK", str(root))
        try:
            status_result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            clean = not status_result.stdout.strip()
            table.add_row(
                "Git working tree",
                "OK" if clean else "WARN",
                "clean" if clean else "has local changes",
            )
        except OSError:
            table.add_row("Git working tree", "WARN", "git executable not available")
    except (ConfigError, RuntimeError) as exc:
        table.add_row("Git repository", "FAIL", str(exc))
        config = TaskToPRConfig()
    if shutil.which("gh") is None:
        table.add_row("GitHub CLI auth", "WARN", "gh not found; install GitHub CLI")
    else:
        try:
            gh = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True, check=False
            )
            table.add_row(
                "GitHub CLI auth",
                "OK" if gh.returncode == 0 else "WARN",
                "authenticated" if gh.returncode == 0 else "run gh auth login",
            )
        except OSError:
            table.add_row("GitHub CLI auth", "WARN", "gh not executable")
    table.add_row("Python", "OK", sys.version.split()[0])
    table.add_row(
        "Node.js", "OK" if shutil.which("node") else "WARN", shutil.which("node") or "not found"
    )
    key_present = provider_api_key(config.agent.provider) is not None
    table.add_row(
        "Model provider",
        "OK" if key_present or config.agent.provider == "demo" else "WARN",
        f"{config.agent.provider}; key {'present' if key_present else 'not found'}",
    )
    console.print(table)


@app.command(name="config")
def show_config() -> None:
    """Validate and print the effective configuration without exposing secrets."""

    try:
        root = git_root(Path.cwd())
        config = load_config(root)
    except (ConfigError, RuntimeError) as exc:
        console.print(f"[bold red]Configuration unavailable:[/] {exc}")
        raise typer.Exit(2) from exc
    console.print_json(data=redacted_config(config))


def main() -> None:
    """Console script entry point."""

    app()
