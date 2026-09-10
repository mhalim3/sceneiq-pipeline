"""Pull Tubi Moments scene rows for a title straight from Databricks.

Replaces the manual query-and-download loop. Requires:
  pip install databricks-sql-connector

Environment (put them in the project .env — it's gitignored):
  DATABRICKS_SERVER_HOSTNAME   e.g. tubi-prod.cloud.databricks.com
  DATABRICKS_HTTP_PATH         SQL warehouse path, e.g. /sql/1.0/warehouses/abc123
  DATABRICKS_TOKEN             personal access token
  SCENEIQ_MOMENTS_QUERY        optional override; default queries
                               core_dev.tubidw.tubi_moments_scene_catalog

The result rows are cached as data/moments/<content_id>.csv — the same format
as a manual Databricks CSV export, consumed by moments.load_moments_csv.

Finding the content_id for a title: `tubi-cms ax content-titles ...` (the
tubi-cms CLI resolves titles against Athena) or CMSUI search.
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

log = logging.getLogger("sceneiq")

_CACHE_DIR = Path("data/moments")

_DEFAULT_QUERY = (
    "select * from core_dev.tubidw.tubi_moments_scene_catalog "
    "where content_id = {content_id}"
)


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

    try:
        from databricks import sql as dbsql
    except ImportError as e:
        raise RuntimeError(
            "databricks-sql-connector is not installed. Run:\n"
            "  pip install databricks-sql-connector"
        ) from e

    missing = [k for k in ("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH",
                           "DATABRICKS_TOKEN") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing env vars for Databricks Moments fetch: {missing}")

    query = os.environ.get("SCENEIQ_MOMENTS_QUERY", _DEFAULT_QUERY).format(
        content_id=content_id, title_id=content_id
    )
    log.info("  querying Databricks: content_id=%s", content_id)
    with dbsql.connect(
        server_hostname=os.environ["DATABRICKS_SERVER_HOSTNAME"],
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
    if not rows:
        raise RuntimeError(f"No Moments rows returned for content_id={content_id}")

    with cached.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows([list(r) for r in rows])
    log.info("  moments cached: %s (%d scenes)", cached, len(rows))
    return cached
