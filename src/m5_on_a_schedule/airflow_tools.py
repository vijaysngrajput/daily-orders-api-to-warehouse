"""Small tools to drive Airflow from Python: the lab notebooks and the tests use them.

Every function here runs a normal `airflow` command inside the Airflow container.
You could type each one in a terminal:

    docker compose exec airflow airflow <command>
"""

import json
import subprocess
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]  # where docker-compose.yml is
DAG = "daily_orders"

HOW_TO_START = (
    "Airflow is not running. Start it from the project folder:\n"
    "  docker compose --profile schedule up -d --build\n"
    "The first start takes a few minutes."
)


def airflow(*args: str, check: bool = True, with_logs: bool = False) -> str:
    """Run one airflow command in the container, and return what it printed.

    `with_logs=True` also returns the log lines, in the order they were written.
    """
    done = subprocess.run(
        ["docker", "compose", "exec", "-T", "airflow", "airflow", *args],
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if with_logs else subprocess.PIPE,
        text=True,
        check=False,
    )
    done.stderr = done.stderr or ""
    if done.returncode != 0 and (
        "is not running" in done.stderr or "no such service" in done.stderr.lower()
    ):
        raise ConnectionError(HOW_TO_START)
    if check and done.returncode != 0:
        raise RuntimeError(
            f"airflow {' '.join(args)} failed:\n{(done.stderr or done.stdout)[-800:]}"
        )
    return done.stdout


def _json(*args: str) -> list[dict]:
    text = airflow(*args, "--output", "json")
    start = text.find("[")
    return json.loads(text[start:]) if start != -1 else []


def wait_until_ready(dag_id: str = DAG, seconds: int = 180) -> None:
    """Wait until Airflow has read the DAG file, then switch the DAG on."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if any(d["dag_id"] == dag_id for d in _json("dags", "list")):
                airflow("dags", "unpause", dag_id, check=False)
                return
        except (RuntimeError, json.JSONDecodeError):
            pass
        time.sleep(3)
    raise TimeoutError(
        f"Airflow did not load the DAG {dag_id} in {seconds} seconds.\n{HOW_TO_START}"
    )


def runs(dag_id: str = DAG) -> list[dict]:
    """Every run of the DAG: its business day (logical date), its kind, and its state. Oldest first."""
    found = [
        {
            "business_day": (run.get("logical_date") or "")[:10],
            "run_id": run["run_id"],
            "kind": run["run_id"].split("__")[0],
            "state": run["state"],
        }
        for run in _json("dags", "list-runs", dag_id)
    ]
    return sorted(found, key=lambda run: (run["business_day"], run["run_id"]))


def wait_for_runs(dag_id: str = DAG, seconds: int = 600) -> list[dict]:
    """Wait until no run of the DAG is queued or running. Returns the runs."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        now = runs(dag_id)
        if now and all(run["state"] in ("success", "failed") for run in now):
            return now
        time.sleep(3)
    raise TimeoutError(f"The runs of {dag_id} did not finish in {seconds} seconds.")


def task_states(business_day: str, dag_id: str = DAG) -> dict[str, str]:
    """The state of each task in the run for one business day."""
    run_id = next(run["run_id"] for run in runs(dag_id) if run["business_day"] == business_day)
    return {t["task_id"]: t["state"] for t in _json("tasks", "states-for-dag-run", dag_id, run_id)}


def backfill(first_day: str, last_day: str, dag_id: str = DAG, wait: bool = True) -> list[dict]:
    """Run the DAG for every business day from `first_day` to `last_day`, one at a time, oldest first.

    Days that already have a run are run again. The command is:
        airflow backfill create --dag-id daily_orders --from-date FIRST --to-date LAST \\
            --max-active-runs 1 --reprocess-behavior completed
    """
    airflow(
        "backfill", "create", "--dag-id", dag_id,
        "--from-date", first_day, "--to-date", f"{last_day}T23:59:59+00:00",
        "--max-active-runs", "1", "--reprocess-behavior", "completed",
    )  # fmt: skip
    return wait_for_runs(dag_id) if wait else runs(dag_id)


def replay(business_day: str, dag_id: str = DAG, wait: bool = True) -> list[dict]:
    """Run one past day again: clear its tasks, and Airflow runs them again.

    The command is:  airflow tasks clear daily_orders -s DAY -e DAY -y
    """
    airflow(
        "tasks",
        "clear",
        dag_id,
        "-s",
        f"{business_day}T00:00:00+00:00",
        "-e",
        f"{business_day}T23:59:59+00:00",
        "-y",
    )
    time.sleep(2)
    return wait_for_runs(dag_id) if wait else runs(dag_id)


def trigger(business_day: str, dag_id: str = DAG, wait: bool = True) -> list[dict]:
    """Start one run by hand, for a business day that has no run yet.

    The command is:  airflow dags trigger daily_orders --logical-date DAYT12:00:00+00:00
    """
    airflow("dags", "trigger", dag_id, "--logical-date", f"{business_day}T12:00:00+00:00")
    time.sleep(2)
    return wait_for_runs(dag_id) if wait else runs(dag_id)


def forget_runs(dag_id: str = DAG) -> None:
    """Delete the DAG's whole run history, and wait until Airflow has read the DAG file again."""
    airflow("dags", "delete", dag_id, "-y", check=False)
    time.sleep(2)
    wait_until_ready(dag_id)
