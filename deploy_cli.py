"""
deploy_cli.py

Separate from cli.py on purpose — generation and deployment are distinct
steps (PRD Section 7: generate once, review once, then deploy/redeploy the
same validated artifact). This script never touches the notebook's
contents, only where it lives and how it's scheduled.

Usage:
    python deploy_cli.py \
        --notebook notebooks/customers_ingest.py \
        --workspace-path /Workspace/Users/you@company.com/ingestion/customers_ingest \
        --job-name customers-daily-ingest \
        --source-path /Volumes/main/bronze/landing/customers.csv \
        --frequency daily --time 02:00 --timezone America/Chicago \
        --run-now

Auth: set DATABRICKS_HOST and DATABRICKS_TOKEN as environment variables
before running this (don't pass them as CLI flags — that puts credentials
in your shell history). See README for how to generate a token.
"""

import argparse

from databricks_deploy import build_cron_expression, deploy_and_schedule


def main():
    parser = argparse.ArgumentParser(description="Deploy a generated notebook as a scheduled Databricks job")
    parser.add_argument("--notebook", required=True, help="Path to the local generated notebook file")
    parser.add_argument("--workspace-path", required=True, help="Destination path inside the Databricks workspace")
    parser.add_argument("--job-name", required=True, help="Job name — reused to find/update this job on future deploys")
    parser.add_argument("--source-path", required=True, help="Databricks Volumes path where the real source file lives at run time")
    parser.add_argument("--frequency", default="daily", choices=["hourly", "daily", "weekly"])
    parser.add_argument("--time", default="02:00", help="HH:MM (24h) for daily/weekly schedules")
    parser.add_argument("--day-of-week", default="MON", help="For weekly schedules, e.g. MON, TUE")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--cluster-id", default=None, help="Use an existing cluster instead of serverless")
    parser.add_argument("--run-now", action="store_true", help="Trigger an immediate run after deploying, to validate")
    args = parser.parse_args()

    cron_expr = build_cron_expression(args.frequency, args.time, args.day_of_week)
    print(f"Schedule: {args.frequency} at {args.time} {args.timezone} -> cron '{cron_expr}'")

    deploy_and_schedule(
        local_notebook_path=args.notebook,
        workspace_path=args.workspace_path,
        job_name=args.job_name,
        source_path_param=args.source_path,
        cron_expression=cron_expr,
        timezone=args.timezone,
        existing_cluster_id=args.cluster_id,
        run_now=args.run_now,
    )


if __name__ == "__main__":
    main()
