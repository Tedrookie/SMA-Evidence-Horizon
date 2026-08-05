"""Scheduling helpers for daily / weekly runs.

The pipeline itself is a single CLI entry point (``main_basic`` or ``main``).
This module documents and generates cron / Windows Task Scheduler snippets,
and can optionally run an in-process loop for local development.

Production recommendation: use OS schedulers (cron / Task Scheduler) that
invoke ``python -m ep_monitor.main_basic`` — more reliable than a long-lived
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
PipelineModule = Literal["main_basic", "main"]

# Default local run time (24h clock) — Monday 08:00 for weekly
_DEFAULT_HOUR = 8
_DEFAULT_MINUTE = 0
_DEFAULT_WEEKDAY = "monday"  # for weekly mode
_DEFAULT_MODULE: PipelineModule = "main_basic"


def normalize_mode(mode: str) -> ScheduleMode:
    """Validate and normalize a schedule mode string."""
    cleaned = (mode or "").strip().lower()
    if cleaned not in {"daily", "weekly"}:
        raise ValueError("schedule mode must be 'daily' or 'weekly'")
    return cleaned  # type: ignore[return-type]


def normalize_module(module: str) -> PipelineModule:
    """Validate pipeline module name."""
    cleaned = (module or "").strip().lower().replace("-", "_")
    if cleaned not in {"main_basic", "main"}:
        raise ValueError("module must be 'main_basic' or 'main'")
    return cleaned  # type: ignore[return-type]


def _weekday_cron(weekday: str = _DEFAULT_WEEKDAY) -> int:
    """Map weekday name to cron DOW (0=Sun … 6=Sat). Monday=1."""
    mapping = {
        "sunday": 0,
        "monday": 1,
        "tuesday": 2,
        "wednesday": 3,
        "thursday": 4,
        "friday": 5,
        "saturday": 6,
    }
    key = (weekday or "monday").strip().lower()
    if key not in mapping:
        raise ValueError(f"weekday must be one of {sorted(mapping)}")
    return mapping[key]


def _weekday_schtasks(weekday: str = _DEFAULT_WEEKDAY) -> str:
    """Map weekday to schtasks /d token (MON, TUE, …)."""
    mapping = {
        "sunday": "SUN",
        "monday": "MON",
        "tuesday": "TUE",
        "wednesday": "WED",
        "thursday": "THU",
        "friday": "FRI",
        "saturday": "SAT",
    }
    key = (weekday or "monday").strip().lower()
    return mapping[key]


def _weekday_powershell(weekday: str = _DEFAULT_WEEKDAY) -> str:
    """Map weekday to PowerShell DaysOfWeek enum name."""
    return (weekday or "monday").strip().capitalize()


def cron_expression(
    mode: str = "weekly",
    *,
    hour: int = _DEFAULT_HOUR,
    minute: int = _DEFAULT_MINUTE,
    weekday: str = _DEFAULT_WEEKDAY,
) -> str:
    """Return a cron expression for ``mode`` ('daily' or 'weekly').

    Daily  → ``minute hour * * *``
    Weekly → ``minute hour * * DOW`` (default Monday 08:00)
    """
    normalized = normalize_mode(mode)
    if normalized == "daily":
        return f"{minute} {hour} * * *"
    return f"{minute} {hour} * * {_weekday_cron(weekday)}"


def cron_entry(
    mode: str = "weekly",
    *,
    python_exe: str | None = None,
    project_root: str | Path | None = None,
    module: str = _DEFAULT_MODULE,
    hour: int = _DEFAULT_HOUR,
    minute: int = _DEFAULT_MINUTE,
    weekday: str = _DEFAULT_WEEKDAY,
    lookback_days: int | None = None,
) -> str:
    """Return a full crontab line that runs the EP monitor pipeline."""
    py = python_exe or sys.executable
    root = Path(project_root) if project_root else Path.cwd()
    mod = normalize_module(module)
    expr = cron_expression(mode, hour=hour, minute=minute, weekday=weekday)
    extra = f" --lookback-days {lookback_days}" if lookback_days else ""
    return (
        f"{expr} cd {root.as_posix()} && "
        f"{Path(py).as_posix()} -m ep_monitor.{mod}{extra} "
        f">> {root.as_posix()}/ep_monitor/data/cron.log 2>&1"
    )


def windows_task_command(
    python_exe: str,
    project_root: str,
    *,
    mode: str = "weekly",
    task_name: str = "EPMonitorSystem",
    module: str = _DEFAULT_MODULE,
    hour: int = _DEFAULT_HOUR,
    minute: int = _DEFAULT_MINUTE,
    weekday: str = _DEFAULT_WEEKDAY,
) -> str:
    """Return a sample ``schtasks`` command for Windows Task Scheduler."""
    normalized = normalize_mode(mode)
    mod = normalize_module(module)
    py = str(Path(python_exe))
    root = str(Path(project_root))
    tr = f'\\"{py}\\" -m ep_monitor.{mod}'
    schedule = "DAILY" if normalized == "daily" else "WEEKLY"
    days_flag = "" if normalized == "daily" else f" /d {_weekday_schtasks(weekday)}"
    st = f"{hour:02d}:{minute:02d}"

    return (
        f'schtasks /Create /F /TN "{task_name}" '
        f'/TR "{tr}" /SC {schedule}{days_flag} '
        f'/ST {st} /RL LIMITED '
        f'/RU "%USERNAME%"'
        f"  &  rem Working directory hint: set Start in = {root}"
    )


def windows_task_powershell(
    python_exe: str,
    project_root: str,
    *,
    mode: str = "weekly",
    task_name: str = "EPMonitorSystem",
    module: str = _DEFAULT_MODULE,
    hour: int = _DEFAULT_HOUR,
    minute: int = _DEFAULT_MINUTE,
    weekday: str = _DEFAULT_WEEKDAY,
    lookback_days: int | None = None,
) -> str:
    """Return a PowerShell snippet that registers the task with a working directory."""
    normalized = normalize_mode(mode)
    mod = normalize_module(module)
    py = str(Path(python_exe))
    root = str(Path(project_root))
    days = "Daily" if normalized == "daily" else "Weekly"
    day_of_week = (
        "" if normalized == "daily" else f" -DaysOfWeek {_weekday_powershell(weekday)}"
    )
    at_time = f"{hour}:{minute:02d}"
    args = f"-m ep_monitor.{mod}"
    if lookback_days:
        args += f" --lookback-days {lookback_days}"

    return f"""$action = New-ScheduledTaskAction -Execute '{py}' -Argument '{args}' -WorkingDirectory '{root}'
$trigger = New-ScheduledTaskTrigger -{days}{day_of_week} -At {at_time}
Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Force
""".strip()


def print_schedule_instructions(
    mode: str = "weekly",
    *,
    python_exe: str | None = None,
    project_root: str | Path | None = None,
    module: str = _DEFAULT_MODULE,
    hour: int = _DEFAULT_HOUR,
    minute: int = _DEFAULT_MINUTE,
    weekday: str = _DEFAULT_WEEKDAY,
    lookback_days: int | None = None,
) -> None:
    """Log human-readable setup instructions for cron and Task Scheduler."""
    py = python_exe or sys.executable
    root = str(Path(project_root) if project_root else Path.cwd())
    normalized = normalize_mode(mode)
    mod = normalize_module(module)

    logger.info("=== Schedule setup (%s · %s) ===", normalized, mod)
    logger.info(
        "Default: %s at %02d:%02d (local time) — change hour/weekday in code or Task Scheduler UI",
        weekday if normalized == "weekly" else "every day",
        hour,
        minute,
    )
    logger.info("Cron expression: %s", cron_expression(normalized, hour=hour, minute=minute, weekday=weekday))
    logger.info(
        "Crontab entry:\n%s",
        cron_entry(
            normalized,
            python_exe=py,
            project_root=root,
            module=mod,
            hour=hour,
            minute=minute,
            weekday=weekday,
            lookback_days=lookback_days,
        ),
    )
    logger.info(
        "Windows schtasks:\n%s",
        windows_task_command(
            py,
            root,
            mode=normalized,
            module=mod,
            hour=hour,
            minute=minute,
            weekday=weekday,
        ),
    )
    logger.info(
        "Windows PowerShell (recommended on this PC):\n%s",
        windows_task_powershell(
            py,
            root,
            mode=normalized,
            module=mod,
            hour=hour,
            minute=minute,
            weekday=weekday,
            lookback_days=lookback_days,
        ),
    )
    logger.info(
        "Tip: keep the PC awake (or plugged in + allow wake timers) at the scheduled time, "
        "and ensure .env has NCBI_EMAIL + SMTP settings."
    )


def run_in_process_loop(
    job: Callable[[], None],
    mode: str = "weekly",
    *,
    run_immediately: bool = False,
    hour: int = _DEFAULT_HOUR,
    minute: int = _DEFAULT_MINUTE,
    weekday: str = _DEFAULT_WEEKDAY,
) -> None:
    """Optional long-running loop using the ``schedule`` library.

    Prefer OS-level cron / Task Scheduler in production; this is for
    local smoke-testing only.
    """
    try:
        import schedule
    except ImportError as exc:
        raise RuntimeError(
            "schedule package is not installed; run: pip install -r requirements.txt"
        ) from exc

    normalized = normalize_mode(mode)
    clock = f"{hour:02d}:{minute:02d}"

    if normalized == "daily":
        schedule.every().day.at(clock).do(job)
    else:
        day_fn = getattr(schedule.every(), weekday.strip().lower())
        day_fn.at(clock).do(job)

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
