"""Transform: run the dbt project. dbt builds the models and runs the tests on the tables."""

import os
from dataclasses import dataclass, field

from dbt.cli.main import dbtRunner

from daily_orders.config import DBT_PROJECT_DIR, PG_HOST, PG_PORT


class TransformFailed(Exception):
    """A model could not be built, or a test on a table failed."""

    def __init__(self, result: "DbtResult"):
        self.result = result
        lines = [f"dbt stopped: {len(result.failures)} problem(s)."]
        lines += [f"  FAILED {name}: {message}" for name, message in result.failures]
        if result.skipped:
            lines.append(
                f"  Not built, because something before it failed: {', '.join(result.skipped)}"
            )
        super().__init__("\n".join(lines))


@dataclass
class DbtResult:
    success: bool
    models_built: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (name, what dbt said)
    skipped: list[str] = field(default_factory=list)  # models that were not built
    statuses: dict[str, str] = field(default_factory=dict)  # every node, and how it ended


def run_dbt(database: str, *args: str) -> DbtResult:
    """Run one dbt command against one database, and sum up what happened.

    `dbt build` runs seeds, models and tests in dependency order: a model is tested
    as soon as it is built, and what depends on a failed test is skipped.
    """
    os.environ["ARDEN_DBT_DATABASE"] = database
    os.environ.setdefault("ARDEN_PG_HOST", PG_HOST)
    os.environ.setdefault("ARDEN_PG_PORT", str(PG_PORT))
    # dbt stays silent. This program reports what happened, in its own words.
    outcome = dbtRunner().invoke(
        [
            "--log-level",
            "none",
            *args,
            "--project-dir",
            str(DBT_PROJECT_DIR),
            "--profiles-dir",
            str(DBT_PROJECT_DIR),
        ]
    )
    result = DbtResult(success=bool(outcome.success))
    if outcome.exception is not None:
        result.failures.append(("dbt", str(outcome.exception).splitlines()[0]))
        return result
    for node in getattr(outcome.result, "results", None) or []:
        name, kind, status = node.node.name, node.node.resource_type, str(node.status)
        result.statuses[name] = status
        if status in ("success", "pass"):
            if kind == "test":
                result.tests_passed += 1
            elif kind in ("model", "seed"):
                result.models_built += 1
        elif status == "skipped":
            if kind != "test":  # a skipped model matters; its skipped tests follow from it
                result.skipped.append(name)
        else:  # error, fail, warn
            if kind == "test":
                result.tests_failed += 1
            result.failures.append((name, (node.message or status).strip().splitlines()[0]))
    return result


def build(database: str, full_refresh: bool = False) -> DbtResult:
    """Build every model and run every test. Raises TransformFailed when anything fails."""
    args = ["build"] + (["--full-refresh"] if full_refresh else [])
    result = run_dbt(database, *args)
    if not result.success:
        raise TransformFailed(result)
    return result


def source_freshness(database: str) -> DbtResult:
    """Ask: is the newest raw row recent enough? (`dbt source freshness`)"""
    return run_dbt(database, "source", "freshness")
