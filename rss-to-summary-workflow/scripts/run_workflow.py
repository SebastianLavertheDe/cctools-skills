#!/usr/bin/env python3
"""Run configured multi-skill workflows and log per-step timings.

Local/cron launcher. Desktop App Workflow Engine uses cctools.skill.yaml
instead and does not invoke this script.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Rotate cron.log before each run to prevent unbounded growth
for _parent in Path(__file__).resolve().parents:
    _shared = _parent / "_shared"
    if _shared.exists():
        sys.path.insert(0, str(_shared))
        from log_rotate import rotate_log
        rotate_log(Path(__file__).resolve().parent.parent / "logs" / "cron.log", max_bytes=5_000_000)
        break


def _resolve_command(command: list[str]) -> list[str]:
    if command and not Path(command[0]).is_absolute():
        resolved = shutil.which(command[0])
        if not resolved:
            for candidate in (
                Path.home() / ".local" / "bin" / command[0],
                Path.home() / ".cargo" / "bin" / command[0],
            ):
                if candidate.exists():
                    resolved = str(candidate)
                    break
        if resolved:
            command = [resolved] + command[1:]
    return command


def _drop_missing_env_files(command: list[str], skill_path: Path) -> list[str]:
    """Remove `--env-file <path>` pairs whose file is absent.

    uv fails hard on missing env files and installed skill copies never ship
    one; keep the pair only when the file actually exists.
    """
    sanitized: list[str] = []
    i = 0
    while i < len(command):
        if command[i] == "--env-file" and i + 1 < len(command):
            env_file = Path(command[i + 1]).expanduser()
            if not env_file.is_absolute():
                env_file = skill_path / env_file
            if env_file.exists():
                sanitized.extend(command[i:i + 2])
            i += 2
            continue
        sanitized.append(command[i])
        i += 1
    return sanitized

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None


DEFAULT_TIMEOUT_SECONDS = None


def _load_config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if yaml:
        data = yaml.safe_load(text)
        return data or {}

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "PyYAML is not installed and workflows.yaml is not JSON-compatible. "
            "Install PyYAML or keep workflows.yaml valid JSON."
        ) from exc


def _find_repo_root(skill_root: Path) -> Path:
    """Skills 仓库根：skill 平铺在顶层，脚本所在 skill 的父目录即根。"""
    return skill_root.parent


def _print_workflows(config: dict) -> None:
    workflows = config.get("workflows", {})
    if not workflows:
        print("No workflows found in workflows.yaml")
        return

    print("Workflows:")
    for name, wf in workflows.items():
        steps = wf.get("steps", []) if isinstance(wf, dict) else []
        step_names = [
            step.get("skill", "") for step in steps if isinstance(step, dict)
        ]
        steps_label = ", ".join([s for s in step_names if s]) or "(no steps)"
        print(f"- {name}: {steps_label}")


def _log_step(log_file: Path, record: dict) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run_step(
    *,
    workflow_name: str,
    step_index: int,
    skill_name: str,
    skill_config: dict,
    repo_root: Path,
    dry_run: bool,
    log_file: Path,
) -> bool:
    path_value = skill_config.get("path")
    command = skill_config.get("command")
    timeout_seconds = skill_config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if timeout_seconds in (None, "", 0):
        timeout_seconds = None

    if not path_value or not command:
        print(f"Step {step_index}: missing path or command for skill '{skill_name}'")
        return False

    skill_path = Path(path_value)
    if not skill_path.is_absolute():
        skill_path = repo_root / skill_path

    if not skill_path.exists():
        print(f"Step {step_index}: skill path does not exist: {skill_path}")
        return False

    command_display = " ".join(command)
    print(f"Step {step_index}: {skill_name}")
    print(f"  cwd: {skill_path}")
    print(f"  cmd: {command_display}")

    if dry_run:
        return True

    start_monotonic = time.monotonic()
    start_time = datetime.now(timezone.utc).isoformat()
    status = "success"
    exit_code = 0

    try:
        resolved_command = _drop_missing_env_files(_resolve_command(command), skill_path)
        # Local/cron path: child skills require Broker-style bindings.
        # 内容根必须由外部注入（crontab 或手动运行时设置 OPENMIND_ROOT）；
        # skills 仓库不内嵌数据，无法推导。
        env = os.environ.copy()
        if not env.get("OPENMIND_ROOT"):
            print(
                "  Error: OPENMIND_ROOT is not set; point it at the "
                "user-selected openmind content root (repo root, flat layout)"
            )
            return False
        if not env.get("OPENMIND_SKILL_DATA_DIR"):
            env["OPENMIND_SKILL_DATA_DIR"] = str(skill_path)
        completed = subprocess.run(
            resolved_command,
            cwd=str(skill_path),
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        if exit_code != 0:
            status = "failed"
    except subprocess.TimeoutExpired:
        status = "timeout"
        exit_code = None
    except Exception as exc:
        status = "error"
        exit_code = None
        print(f"  Error running step {step_index}: {exc}")

    end_time = datetime.now(timezone.utc).isoformat()
    duration_seconds = round(time.monotonic() - start_monotonic, 3)

    record = {
        "workflow": workflow_name,
        "step": step_index,
        "skill": skill_name,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": duration_seconds,
        "status": status,
        "exit_code": exit_code,
    }
    _log_step(log_file, record)

    print(
        f"  status: {status} | exit_code: {exit_code} | duration: {duration_seconds}s"
    )

    return status == "success"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run configured skill workflows")
    parser.add_argument("--list", action="store_true", help="List workflows")
    parser.add_argument("--workflow", type=str, help="Workflow name to run")
    parser.add_argument("--dry-run", action="store_true", help="Print steps only")
    args = parser.parse_args()

    skill_root = Path(__file__).resolve().parents[1]
    config_path = skill_root / "workflows.yaml"
    if not config_path.exists():
        print(f"Missing workflows.yaml at {config_path}")
        return 1

    config = _load_config(config_path)
    if args.list:
        _print_workflows(config)
        return 0

    if not args.workflow:
        print("Use --workflow <name> or --list")
        return 1

    workflows = config.get("workflows", {})
    skills = config.get("skills", {})
    if args.workflow not in workflows:
        print(f"Workflow not found: {args.workflow}")
        _print_workflows(config)
        return 1

    steps = workflows[args.workflow].get("steps", [])
    if not steps:
        print(f"Workflow '{args.workflow}' has no steps")
        return 1

    repo_root = _find_repo_root(skill_root)
    log_file = skill_root / "logs" / "workflow_runs.jsonl"

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or "skill" not in step:
            print(f"Step {idx}: invalid step definition")
            return 1

        skill_name = step["skill"]
        skill_config = skills.get(skill_name)
        if not skill_config:
            print(f"Step {idx}: skill not found in skills config: {skill_name}")
            return 1

        ok = _run_step(
            workflow_name=args.workflow,
            step_index=idx,
            skill_name=skill_name,
            skill_config=skill_config,
            repo_root=repo_root,
            dry_run=args.dry_run,
            log_file=log_file,
        )
        if not ok:
            print("Workflow stopped due to failure")
            return 1

    print("Workflow completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
