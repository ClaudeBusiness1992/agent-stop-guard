#!/usr/bin/env python3
"""Install Agent Stop Guard without overwriting existing hook configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
from datetime import datetime, timezone


PROJECT_ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def contains_command(value: object, marker: str) -> bool:
    if isinstance(value, dict):
        return any(contains_command(item, marker) for item in value.values())
    if isinstance(value, list):
        return any(contains_command(item, marker) for item in value)
    return isinstance(value, str) and marker in value


def replace_hook_command(value: object, script_name: str, command: str) -> bool:
    """Migriert nur den verwalteten Hook, ohne andere Hooks anzufassen."""
    changed = False
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "command" and isinstance(item, str) and script_name in item:
                if item != command:
                    value[key] = command
                    changed = True
                continue
            changed = replace_hook_command(item, script_name, command) or changed
    elif isinstance(value, list):
        for item in value:
            changed = replace_hook_command(item, script_name, command) or changed
    return changed


def append_hook(
    config: dict,
    event: str,
    command: str,
    *,
    matcher: str | None = None,
) -> bool:
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be a JSON object")
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        raise ValueError(f"hooks.{event} must be a JSON array")
    if contains_command(entries, command):
        return False
    entry: dict[str, object] = {
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": 15,
            }
        ]
    }
    if matcher is not None:
        entry["matcher"] = matcher
    entries.append(entry)
    return True


def write_json_with_backup(path: Path, value: dict, *, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.bak-agent-stop-guard-{stamp}")
        shutil.copy2(path, backup)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def copy_file(source: Path, target: Path, mode: int, *, dry_run: bool) -> None:
    if dry_run:
        return
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with source.open("rb") as reader, tempfile.NamedTemporaryFile(
        mode="wb",
        dir=target.parent,
        prefix=f".{target.name}.",
        delete=False,
    ) as handle:
        shutil.copyfileobj(reader, handle)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    try:
        os.chmod(temp_path, mode)
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def install(
    platforms: set[str],
    *,
    include_ask_user_guard: bool = False,
    dry_run: bool = False,
    home: Path | None = None,
) -> list[str]:
    home = (home or Path.home()).resolve()
    install_root = home / ".local" / "share" / "agent-stop-guard"
    stop_source = PROJECT_ROOT / "hooks" / "stop-open-items-guard.py"
    ask_source = PROJECT_ROOT / "hooks" / "ask-user-open-items-guard.py"
    stop_target = install_root / stop_source.name
    ask_target = install_root / ask_source.name
    actions: list[str] = []

    if platforms & {"claude", "codex"}:
        copy_file(stop_source, stop_target, 0o755, dry_run=dry_run)
        actions.append(f"install {stop_target}")
        # Bereits laufende ältere Sessions können den direkten Hookpfad beim
        # Start gecacht haben. Die identische Kompatibilitätskopie verhindert,
        # dass sie bis zum Neustart noch die alte Policy ausführen.
        legacy_stop_target = home / ".claude" / "hooks" / stop_source.name
        copy_file(stop_source, legacy_stop_target, 0o755, dry_run=dry_run)
        actions.append(f"install compatibility copy {legacy_stop_target}")
    if include_ask_user_guard and "claude" in platforms:
        copy_file(ask_source, ask_target, 0o755, dry_run=dry_run)
        actions.append(f"install {ask_target}")

    stop_command = f"python3 {shlex.quote(str(stop_target))}"
    if "claude" in platforms:
        path = home / ".claude" / "settings.json"
        config = read_json(path)
        changed = replace_hook_command(
            config, stop_source.name, stop_command
        )
        changed = append_hook(config, "Stop", stop_command) or changed
        if include_ask_user_guard:
            ask_command = f"python3 {shlex.quote(str(ask_target))}"
            changed = append_hook(
                config,
                "PreToolUse",
                ask_command,
                matcher="AskUserQuestion",
            ) or changed
        if changed:
            write_json_with_backup(path, config, dry_run=dry_run)
            actions.append(f"merge {path}")

    if "codex" in platforms:
        path = home / ".codex" / "hooks.json"
        config = read_json(path)
        changed = replace_hook_command(
            config, stop_source.name, stop_command
        )
        changed = append_hook(config, "Stop", stop_command) or changed
        if changed:
            write_json_with_backup(path, config, dry_run=dry_run)
            actions.append(f"merge {path}")

    if "opencode" in platforms:
        source = PROJECT_ROOT / "plugins" / "stop-open-items-guard.ts"
        target = home / ".config" / "opencode" / "plugins" / source.name
        copy_file(source, target, 0o644, dry_run=dry_run)
        actions.append(f"install {target}")

    return actions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude", action="store_true")
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--opencode", action="store_true")
    parser.add_argument(
        "--ask-user-guard",
        action="store_true",
        help="also prevent premature Claude AskUserQuestion calls",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    platforms = {
        name
        for name in ("claude", "codex", "opencode")
        if getattr(args, name)
    }
    if not platforms:
        raise SystemExit("Select at least one of --claude, --codex or --opencode")
    for action in install(
        platforms,
        include_ask_user_guard=args.ask_user_guard,
        dry_run=args.dry_run,
    ):
        print(("would " if args.dry_run else "") + action)
    print("Restart the selected agent sessions to load the guard.")


if __name__ == "__main__":
    main()
