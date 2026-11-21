# The image for milestone 5: Airflow, plus the pipeline in its own virtual environment.
#
# Airflow and dbt each need many Python packages, and their versions do not always agree.
# So the pipeline does not share Airflow's Python. It gets its own environment, /opt/arden-venv,
# and Airflow starts it as a command: /opt/arden-venv/bin/python -m daily_orders ...
FROM apache/airflow:3.4.0

USER root
RUN python -m venv /opt/arden-venv && chown -R airflow /opt/arden-venv
USER airflow

COPY requirements.txt /tmp/arden-requirements.txt
RUN /opt/arden-venv/bin/pip install --no-cache-dir -r /tmp/arden-requirements.txt
