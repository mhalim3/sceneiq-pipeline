"""Pull Tubi Moments scene rows for a title straight from Databricks.

Replaces the manual query-and-download loop. Requires:
  pip install databricks-sql-connector

Environment (put them in the project .env — it's gitignored):
  DATABRICKS_HOST              e.g. tubi-dev.cloud.databricks.com
                               (DATABRICKS_SERVER_HOSTNAME also accepted)
  DATABRICKS_HTTP_PATH         SQL warehouse path, e.g. /sql/1.0/warehouses/abc123
  DATABRICKS_TOKEN             personal access token
  DATABRICKS_CATALOG           default core_dev
  DATABRICKS_SCHEMA            default tubidw
  DATABRICKS_CONTENT_TABLE     default content_info (title -> content_id lookup)
  SCENEIQ_MOMENTS_QUERY        optional full-query override

The result rows are cached as data/moments/<content_id>.csv — the same format
as a manual Databricks CSV export, consumed by moments.load_moments_csv.
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

log = logging.getLogger("sceneiq")

_CACHE_DIR = Path("data/moments")

_DEFAULT_QUERY = (
    "select * from {catalog}.{schema}.tubi_moments_scene_catalog "
    "where content_id = {content_id}"
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _host() -> str:
    return _env("DATABRICKS_HOST") or _env("DATABRICKS_SERVER_HOSTNAME")


def _connect():
    try:
        from databricks import sql as dbsql
    except ImportError as e:
        raise RuntimeError(
            "databricks-sql-connector is not installed. Run:\n"
            "  pip install databricks-sql-connector"
        ) from e
    missing = [n for n, v in [
        ("DATABRICKS_HOST", _host()),
        ("DATABRICKS_HTTP_PATH", _env("DATABRICKS_HTTP_PATH")),
        ("DATABRICKS_TOKEN", _env("DATABRICKS_TOKEN")),
    ] if not v]
    if missing:
        raise RuntimeError(f"Missing env vars for Databricks: {missing}")
    return dbsql.connect(
        server_hostname=_host(),
        http_path=_env("DATABRICKS_HTTP_PATH"),
        access_token=_env("DATABRICKS_TOKEN"),
    )


def _run_query(query: str) -> tuple[list[str], list]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            columns = [d[0] for d in cur.description]
            return columns, cur.fetchall()


def resolve_content_id(title: str) -> list[dict]:
    """Look up candidate content_ids for a title in the content table."""
    catalog = _env("DATABRICKS_CATALOG", "core_dev")
    schema = _env("DATABRICKS_SCHEMA", "tubidw")
    table = _env("DATABRICKS_CONTENT_TABLE", "content_info")
    safe_title = title.replace("'", "''")
    query = (
        f"select content_id, content_name, content_type "
        f"from {catalog}.{schema}.{table} "
        f"where lower(content_name) like lower('%{safe_title}%') "
        f"limit 10"
    )
    log.info("  resolving title %r via %s.%s.%s", title, catalog, schema, table)
    columns, rows = _run_query(query)
    return [dict(zip(columns, r)) for r in rows]


def fetch_moments(content_id: str, refresh: bool = False) -> Path:
    """Fetch Moments scene rows for `content_id`, cache as CSV, return path."""
    if not str(content_id).isdigit():
        raise RuntimeError(
            f"content_id must be numeric (got {content_id!r}). Resolve a title "
            "to its content_id via tubi-cms or CMSUI first."
        )
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _CACHE_DIR / f"{content_id}.csv"
    if cached.exists() and not refresh:
        log.info("  moments cache hit: %s", cached)
        return cached

    query = _env("SCENEIQ_MOMENTS_QUERY") or _DEFAULT_QUERY
    query = query.format(
        content_id=content_id,
        title_id=content_id,
        catalog=_env("DATABRICKS_CATALOG", "core_dev"),
        schema=_env("DATABRICKS_SCHEMA", "tubidw"),
    )
    log.info("  querying Databricks: content_id=%s", content_id)
    columns, rows = _run_query(query)
    if not rows:
        raise RuntimeError(f"No Moments rows returned for content_id={content_id}")

    with cached.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows([list(r) for r in rows])
    log.info("  moments cached: %s (%d scenes)", cached, len(rows))
    return cached
