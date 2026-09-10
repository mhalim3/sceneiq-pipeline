"""Pull Tubi Moments JSON for a title straight from Databricks.

Replaces the manual query-and-download loop. Requires:
  pip install databricks-sql-connector

Environment (put them in the project .env — it's gitignored):
  DATABRICKS_SERVER_HOSTNAME   e.g. tubi-prod.cloud.databricks.com
  DATABRICKS_HTTP_PATH         SQL warehouse path, e.g. /sql/1.0/warehouses/abc123
  DATABRICKS_TOKEN             personal access token
  SCENEIQ_MOMENTS_QUERY        SQL returning ONE row/column of Moments JSON for
                               a title; {title_id} is substituted. Example:
                               SELECT moments_json FROM catalog.schema.moments
                               WHERE title_id = '{title_id}'

Fetched payloads cache to data/moments/<title_id>.json so repeat runs and the
review workflow don't re-query the warehouse.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("sceneiq")

_CACHE_DIR = Path("data/moments")


def fetch_moments(title_id: str, refresh: bool = False) -> Path:
    """Fetch Moments JSON for `title_id`, cache it, return the file path."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _CACHE_DIR / f"{title_id}.json"
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
                           "DATABRICKS_TOKEN", "SCENEIQ_MOMENTS_QUERY")
               if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing env vars for Databricks Moments fetch: {missing}")

    query = os.environ["SCENEIQ_MOMENTS_QUERY"].format(title_id=title_id)
    log.info("  querying Databricks for moments: title_id=%s", title_id)
    with dbsql.connect(
        server_hostname=os.environ["DATABRICKS_SERVER_HOSTNAME"],
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
    if not row or row[0] is None:
        raise RuntimeError(f"No Moments row returned for title_id={title_id}")

    payload = row[0]
    if isinstance(payload, str):
        payload = json.loads(payload)
    cached.write_text(json.dumps(payload, ensure_ascii=False))
    log.info("  moments cached: %s (%d scenes)", cached, len(payload.get("scenes", [])))
    return cached
