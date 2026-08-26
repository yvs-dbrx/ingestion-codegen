"""
cli.py

Usage — local testing (file on this machine, for trying out codegen changes):
    python cli.py \
        --source-type csv --file sample_data/sample.csv --location local \
        --catalog my_catalog --schema bronze --table customers \
        --output notebooks/customers_ingest.py

Usage — real client source (Databricks Volume or S3, the actual scenario):
    python cli.py \
        --source-type csv --file /Volumes/main/landing/customers.csv \
        --catalog my_catalog --schema bronze --table customers \
        --output notebooks/customers_ingest.py

--location defaults to databricks_workspace (the real scenario) — you
must pass --location local explicitly to use a file on your own machine.
No SQL warehouse needed: schema discovery runs as a one-time job on
serverless compute by default (pass --cluster-id to use an existing
cluster instead).
"""

import argparse

from codegen import generate_notebook


def main():
    parser = argparse.ArgumentParser(description="Ingestion codegen prototype")
    parser.add_argument("--source-type", required=True, choices=["csv", "json"])
    parser.add_argument("--file", required=True, help="Local path (--location local) or Volume/S3 path")
    parser.add_argument("--location", default="databricks_workspace", choices=["local", "databricks_workspace"])
    parser.add_argument("--cluster-id", default=None, help="Use an existing cluster for schema discovery instead of serverless")
    parser.add_argument("--delimiter", default=",", help="CSV only")
    parser.add_argument("--no-header", action="store_true", help="CSV only: source has no header row")
    parser.add_argument("--multiline", action="store_true", help="JSON only: single document (object or array) rather than NDJSON")
    parser.add_argument("--catalog", required=True, help="Unity Catalog catalog name")
    parser.add_argument("--schema", required=True, help="Target schema (e.g. 'bronze')")
    parser.add_argument("--table", required=True, help="Target table name")
    parser.add_argument("--output", required=True, help="Where to write the generated notebook")
    args = parser.parse_args()

    source_config = {
        "file_path": args.file,
        "location": args.location,
        "delimiter": args.delimiter,
        "has_header": not args.no_header,
        "multiline": args.multiline,
    }

    if args.location == "databricks_workspace" and args.cluster_id:
        source_config["existing_cluster_id"] = args.cluster_id

    generate_notebook(
        source_type=args.source_type,
        source_config=source_config,
        catalog=args.catalog,
        target_schema=args.schema,
        table=args.table,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
