"""PostgreSQL access: schema introspection and read-only query execution."""

from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg
from psycopg import sql as pgsql

from .denylist import DenyList

SCHEMA_QUERY = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
  AND t.table_type IN ('BASE TABLE', 'VIEW')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


def describe_schema(conn: psycopg.Connection, denylist: DenyList) -> str:
    """Return a compact text schema, omitting deny-listed tables entirely."""
    tables: dict[str, list[str]] = {}
    with conn.cursor() as cur:
        cur.execute(SCHEMA_QUERY)
        for schema, table, column, dtype in cur.fetchall():
            if denylist.is_denied(table, schema):
                continue
            tables.setdefault(f"{schema}.{table}", []).append(f"{column} {dtype}")
    conn.rollback()
    return "\n".join(f"{name}({', '.join(cols)})" for name, cols in tables.items())


def run_readonly(
    conn: psycopg.Connection, query: str, max_rows: int, timeout_ms: int
) -> QueryResult:
    """Execute ``query`` inside a READ ONLY transaction that is always rolled back."""
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                pgsql.SQL("SET LOCAL statement_timeout = {}").format(pgsql.Literal(timeout_ms))
            )
            cur.execute(query)
            if cur.description is None:
                return QueryResult(columns=[], rows=[], truncated=False)
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)
            return QueryResult(columns, rows[:max_rows], truncated=len(rows) > max_rows)
    finally:
        conn.rollback()


def planned_relations(conn: psycopg.Connection, query: str, timeout_ms: int) -> set[tuple[str, str]]:
    """Return every (schema, table) the planner will scan, without executing the query.

    Unlike parsing the SQL text, this sees through views, so a view built on a
    deny-listed table is caught too.
    """
    found: set[tuple[str, str]] = set()

    def walk(node: dict) -> None:
        if "Relation Name" in node:
            found.add((node.get("Schema", "public"), node["Relation Name"]))
        for child in node.get("Plans", []):
            walk(child)

    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                pgsql.SQL("SET LOCAL statement_timeout = {}").format(pgsql.Literal(timeout_ms))
            )
            cur.execute("EXPLAIN (VERBOSE, FORMAT JSON) " + query)
            plan = cur.fetchone()[0]
            if isinstance(plan, str):
                plan = json.loads(plan)
            walk(plan[0]["Plan"])
    finally:
        conn.rollback()
    return found
