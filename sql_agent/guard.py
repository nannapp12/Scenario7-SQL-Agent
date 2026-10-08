"""Static checks run on every generated query before it reaches the database."""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from .denylist import DenyList

# Functions that read files, reach other servers, or change server state.
BLOCKED_FUNCTIONS = frozenset(
    {
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "pg_sleep",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "set_config",
        "query_to_xml",
        "query_to_json",
        "table_to_xml",
        "cursor_to_xml",
    }
)

WRITE_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Into,  # SELECT ... INTO new_table
    exp.Lock,
)


class QueryRejected(Exception):
    """Raised when a query fails a safety check. ``denied`` is set for deny-list hits."""

    def __init__(self, message: str, denied: list[str] | None = None):
        super().__init__(message)
        self.denied = denied or []


@dataclass(frozen=True)
class CheckedQuery:
    sql: str
    tables: list[str]  # schema-qualified tables the query reads


def check_query(sql: str, denylist: DenyList) -> CheckedQuery:
    """Validate ``sql`` and return the tables it reads, or raise ``QueryRejected``."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise QueryRejected(f"Could not parse SQL: {e}") from e

    if len(statements) != 1:
        raise QueryRejected(f"Expected exactly one statement, got {len(statements)}.")
    tree = statements[0]

    if not isinstance(tree, exp.Query):
        raise QueryRejected(f"Only read-only SELECT queries are allowed (got {tree.key.upper()}).")
    for node in tree.walk():
        if isinstance(node, WRITE_NODES):
            raise QueryRejected(f"Query contains a write/DDL clause ({node.key.upper()}).")
        if isinstance(node, (exp.Anonymous, exp.Func)):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in BLOCKED_FUNCTIONS:
                raise QueryRejected(f"Function {name}() is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables, denied = [], []
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name:
            continue  # e.g. table-valued function such as generate_series()
        schema = table.db or None
        if schema is None and name.lower() in cte_names:
            continue
        qualified = f"{schema or 'public'}.{name}".lower()
        if qualified not in tables:
            tables.append(qualified)
        if denylist.is_denied(name, schema) and qualified not in denied:
            denied.append(qualified)

    if denied:
        raise QueryRejected(
            "Query references table(s) on the do-not-query list: " + ", ".join(denied),
            denied=denied,
        )
    return CheckedQuery(sql=sql.strip().rstrip(";"), tables=tables)
