"""Headless batch runner for a selected regression input."""

from __future__ import annotations

import asyncio
import argparse
from pathlib import Path

from src.config.settings import AppConfig
from src.services.models import JobRequest
from src.services.task_runner import TaskRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def main(input_path: Path, label: str) -> int:
    # from_environment picks up the user-local encrypted auth store and the
    # legacy login-state path, exactly like the desktop app.
    config = AppConfig.from_environment(PROJECT_ROOT)
    runner = TaskRunner(config)
    result = await runner.run(
        JobRequest(
            input_path=input_path,
            label=label,
        )
    )
    print(f"job_id={result.job_id}")
    print(f"archive={result.archive_path}")
    print(f"records={len(result.records)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "tests" / "test_input" / "real_url_coverage_50.csv",
    )
    parser.add_argument("--label", default="代码修改后复跑")
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(arguments.input.resolve(), arguments.label)))
