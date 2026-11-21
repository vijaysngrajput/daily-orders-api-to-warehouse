"""A lab: the same pipeline as a micro-batch. It runs every five minutes.

It starts paused. Nothing here is needed for the daily report.
It writes to its own database, so it cannot disturb the daily DAG.
"""

from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

PYTHON = "/opt/arden-venv/bin/python"
# Its own database, and no lookback: with a five-minute slice, each run should read five minutes.
PIPELINE = (
    "cd /opt/arden/src/m5_on_a_schedule && "
    f"ARDEN_DATABASE=arden_m5_micro ARDEN_LOOKBACK_DAYS=0 {PYTHON} -m daily_orders"
)

with DAG(
    dag_id="orders_every_five_minutes",
    description="Lab: the same batch, with a five-minute slice.",
    schedule="*/5 * * * *",  # every five minutes
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["arden", "lab"],
) as dag:
    # Five minutes pass in the fake order system, as they do on the wall.
    five_minutes_pass = BashOperator(
        task_id="five_minutes_pass",
        bash_command=f"cd /opt/arden && {PYTHON} -m order_system.clock --advance-minutes 5",
    )
    extract_and_load = BashOperator(
        task_id="extract_and_load", bash_command=f"{PIPELINE} extract-load"
    )
    transform_and_test = BashOperator(
        task_id="transform_and_test", bash_command=f"{PIPELINE} transform-test"
    )

    five_minutes_pass >> extract_and_load >> transform_and_test
