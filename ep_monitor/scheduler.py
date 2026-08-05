"""Scheduling helpers for daily / weekly runs.

The pipeline itself is a single CLI entry point (``main.py``). This module
documents and generates cron / Windows Task Scheduler snippets, and can
optionally run an in-process loop for local development.

Production recommendation: use OS schedulers (cron / Task Scheduler) that
invoke ``python -m ep_monitor.main`` — more reliable than a long-lived
Python process.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Callable, Literal

logger = logging.getLogger(__name__)

ScheduleMode = Literal["daily", "weekly"]

# Default local run time (24h clock)
_DEFAULT_HOUR = 7
_DEFAULT_MINUTE = 0


def normalize_mode(mode: str) -> ScheduleMode:
    """Validate and normalize a schedule mode string."""
    cleaned = (mode or "").strip().lower()
    if cleaned not in {"daily", "weekly"}:
        raise ValueError("schedule mode must be 'daily' or 'weekly'")
    return cleaned  # type: ignore[return-value]


def cron_expression(mode: str = "weekly") -> str:
    """Return a cron expression for ``mode`` ('daily' or 'weekly').

    Daily  → 07:00 every day
    Weekly → 07:00 every Monday
    """
    normalized = normalize_mode(mode)
    minute = _DEFAULT_MINUTE
    hour = _DEFAULT_HOUR
    if normalized == "daily":
        return f"{minute} {hour} * * *"
    return f"{minute} {hour} * * 1"


def cron_entry(
    mode: str = "weekly",
    *,
    python_exe: str | None = None,
    project_root: str | Path | None = None,
) -> str:
    """Return a full crontab line that runs the EP monitor pipeline."""
    py = python_exe or sys.executable
    root = Path(project_root) if project_root else Path.cwd()
    expr = cron_expression(mode)
    # cd into project so relative data/reports paths and .env loading work
    return (
        f"{expr} cd {root.as_posix()} && "
        f"{Path(py).as_posix()} -m ep_monitor.main "
        f">> {root.as_posix()}/ep_monitor/data/cron.log 2>&1"
    )


def windows_task_command(
    python_exe: str,
    project_root: str,
    *,
    mode: str = "weekly",
    task_name: str = "EPMarketNewsMonitor",
) -> str:
    """Return a sample ``schtasks`` command for Windows Task Scheduler.

    Args:
        python_exe: Absolute path to the Python interpreter (preferably venv).
        project_root: Repository root containing the ``ep_monitor`` package.
        mode: ``daily`` or ``weekly``.
        task_name: Windows scheduled-task name.
    """
    normalized = normalize_mode(mode)
    py = str(Path(python_exe))
    root = str(Path(project_root))
    # schtasks /TR needs the entire command line inside one quoted string
    tr = f'\\"{py}\\" -m ep_monitor.main'
    schedule = "DAILY" if normalized == "daily" else "WEEKLY"
    days_flag = "" if normalized == "daily" else " /d MON"

    return (
        f'schtasks /Create /F /TN "{task_name}" '
        f'/TR "{tr}" /SC {schedule}{days_flag} '
        f'/ST 07:00 /RL LIMITED '
        f'/RU "%USERNAME%"'
        f'  &  rem Working directory hint: set Start in = {root}'
    )


def windows_task_powershell(
    python_exe: str,
    project_root: str,
    *,
    mode: str = "weekly",
    task_name: str = "EPMarketNewsMonitor",
) -> str:
    """Return a PowerShell snippet that registers the task with a working directory."""
    normalized = normalize_mode(mode)
    py = str(Path(python_exe))
    root = str(Path(project_root))
    days = "Daily" if normalized == "daily" else "Weekly"
    day_of_week = "" if normalized == "daily" else " -DaysOfWeek Monday"

    return f"""$action = New-ScheduledTaskAction -Execute '{py}' -Argument '-m ep_monitor.main' -WorkingDirectory '{root}'
$trigger = New-ScheduledTaskTrigger -{days}{day_of_week} -At 7:00am
Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Force
""".strip()


def print_schedule_instructions(
    mode: str = "weekly",
    *,
    python_exe: str | None = None,
    project_root: str | Path | None = None,
) -> None:
    """Log human-readable setup instructions for cron and Task Scheduler."""
    py = python_exe or sys.executable
    root = str(Path(project_root) if project_root else Path.cwd())
    normalized = normalize_mode(mode)

    logger.info("=== Schedule setup (%s) ===", normalized)
    logger.info("Cron expression: %s", cron_expression(normalized))
    logger.info("Crontab entry:\n%s", cron_entry(normalized, python_exe=py, project_root=root))
    logger.info(
        "Windows schtasks:\n%s",
        windows_task_command(py, root, mode=normalized),
    )
    logger.info(
        "Windows PowerShell:\n%s",
        windows_task_powershell(py, root, mode=normalized),
    )


def run_in_process_loop(
    job: Callable[[], None],
    mode: str = "weekly",
    *,
    run_immediately: bool = False,
) -> None:
    """Optional long-running loop using the ``schedule`` library.

    Prefer OS-level cron / Task Scheduler in production; this is for
    local smoke-testing only.

    Args:
        job: Zero-arg callable that runs one pipeline cycle.
        mode: ``daily`` or ``weekly``.
        run_immediately: If True, execute ``job`` once before waiting.
    """
    try:
        import schedule
    except ImportError as exc:
        raise RuntimeError(
            "schedule package is not installed; run: pip install -r requirements.txt"
        ) from exc

    normalized = normalize_mode(mode)
    clock = f"{_DEFAULT_HOUR:02d}:{_DEFAULT_MINUTE:02d}"

    if normalized == "daily":
        schedule.every().day.at(clock).do(job)
    else:
        schedule.every().monday.at(clock).do(job)

    logger.info(
        "In-process scheduler started (%s at %s). Prefer OS cron/Task Scheduler in production.",
        normalized,
        clock,
    )

    if run_immediately:
        logger.info("Running job immediately (run_immediately=True)")
        job()

    while True:
        schedule.run_pending()
        time.sleep(30)
