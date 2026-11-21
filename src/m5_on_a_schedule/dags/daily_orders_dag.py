"""The daily orders pipeline, on a schedule.

One DAG, four tasks, three arrows:

    set_source_clock  >>  extract_and_load  >>  transform_and_test  >>  publish_report

Airflow decides WHEN each task runs, in WHAT ORDER, and what happens when one FAILS.
The work itself is the same program as before: `python -m daily_orders <step>`.
"""

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, CronDataIntervalTimetable

ARDEN = "America/Chicago"
PYTHON = "/opt/arden-venv/bin/python"  # the pipeline's own virtual environment, inside the image
PIPELINE = f"cd /opt/arden/src/m5_on_a_schedule && {PYTHON} -m daily_orders"

# Every run is FOR one business day: its logical date. In templates that day is {{ ds }}.
# The run HAPPENS the morning after, when that day is over. That is the same for a
# scheduled run, a backfill, and a run a person triggers by hand.
BUSINESS_DAY = "{{ ds }}"
RUN_DATE = "{{ macros.ds_add(ds, 1) }}"

with DAG(
    dag_id="daily_orders",
    description="Load new and changed orders every day, safe to run twice, with tests on the final table.",
    # Every day at 07:00 in Arden. Each run covers the 24 hours before it: its data interval.
    # The interval of business day 7 June is 7 June 07:00 to 8 June 07:00, and the run starts when it ends.
    schedule=CronDataIntervalTimetable("0 7 * * *", timezone=ARDEN),
    start_date=datetime(2026, 6, 1, 12, 0),  # business day 1 June. Its run is the morning of 2 June
    end_date=datetime(2026, 6, 17, 12, 0),  # the last business day with a run
    catchup=False,  # do not run every missed morning by itself. A person asks for a backfill
    max_active_runs=1,  # one morning at a time. Two runs must never write the same tables together
    default_args={"retries": 0},
    tags=["arden", "batch"],
) as dag:
    # The order system in this project is a fake with a clock you control.
    # This task moves it to the morning of the run. A real source needs no such task.
    set_source_clock = BashOperator(
        task_id="set_source_clock",
        bash_command=f"cd /opt/arden && {PYTHON} -m order_system.clock --morning {RUN_DATE}",
    )

    # Read what changed and land it raw. The API can be down, so this task may be tried again.
    extract_and_load = BashOperator(
        task_id="extract_and_load",
        bash_command=f"{PIPELINE} extract-load --run-date {RUN_DATE}",
        retries=3,
        retry_delay=timedelta(seconds=20),
    )

    # Build the dbt models and test the tables. No retries: a test that failed on the data
    # will fail again. A person must look.
    transform_and_test = BashOperator(
        task_id="transform_and_test",
        bash_command=f"{PIPELINE} transform-test",
    )

    publish_report = BashOperator(
        task_id="publish_report",
        bash_command=f"{PIPELINE} report",
    )

    set_source_clock >> extract_and_load >> transform_and_test >> publish_report
