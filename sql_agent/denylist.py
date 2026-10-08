"""Loads and matches the "do not query" list."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DenyList:
    """Tables the agent must never read.

    Entries are either ``table`` (blocked in every schema) or ``schema.table``.
    ``schema.*`` blocks an entire schema. Matching is case-insensitive.
    """

    tables: frozenset[str] = field(default_factory=frozenset)  # bare table names
    qualified: frozenset[str] = field(default_factory=frozenset)  # schema.table
    schemas: frozenset[str] = field(default_factory=frozenset)  # schema.*

    @classmethod
    def from_entries(cls, entries: list[str]) -> "DenyList":
        tables, qualified, schemas = set(), set(), set()
        for raw in entries:
            entry = raw.split("#", 1)[0].strip().strip('"').lower()
            if not entry:
                continue
            if entry.endswith(".*"):
                schemas.add(entry[:-2])
            elif "." in entry:
                qualified.add(entry)
            else:
                tables.add(entry)
        return cls(frozenset(tables), frozenset(qualified), frozenset(schemas))

    @classmethod
    def from_file(cls, path: str | Path) -> "DenyList":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"Deny list not found at {p}. Create it (it may be empty) or set DENYLIST_PATH."
            )
        return cls.from_entries(p.read_text().splitlines())

    def is_denied(self, table: str, schema: str | None = None) -> bool:
        table = table.lower()
        schema = (schema or "public").lower()
        return (
            table in self.tables
            or f"{schema}.{table}" in self.qualified
            or schema in self.schemas
        )
