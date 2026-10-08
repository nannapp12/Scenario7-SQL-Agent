"""Ties translation, safety checks, and execution together."""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from .db import QueryResult, describe_schema, planned_relations, run_readonly
from .denylist import DenyList
from .guard import QueryRejected, check_query
from .translator import Translator


@dataclass
class AgentAnswer:
    question: str
    sql: str = ""
    explanation: str = ""
    result: QueryResult | None = None
    error: str | None = None
    blocked_tables: list[str] = field(default_factory=list)


class SQLAgent:
    def __init__(
        self,
        conn: psycopg.Connection,
        denylist: DenyList,
        translator: Translator | None = None,
        max_rows: int = 200,
        timeout_ms: int = 15000,
        max_attempts: int = 3,
    ):
        self.conn = conn
        self.denylist = denylist
        self.translator = translator or Translator(describe_schema(conn, denylist))
        self.max_rows = max_rows
        self.timeout_ms = timeout_ms
        self.max_attempts = max_attempts

    def ask(self, question: str) -> AgentAnswer:
        answer = AgentAnswer(question=question)
        feedback: list[tuple[str, str]] = []

        for _ in range(self.max_attempts):
            translation = self.translator.translate(question, feedback)
            answer.sql, answer.explanation = translation.sql, translation.explanation
            if not translation.sql:
                answer.error = translation.explanation or "The question can't be answered from this database."
                return answer

            # 1. Parse the SQL: read-only, single statement, no deny-listed tables.
            try:
                checked = check_query(translation.sql, self.denylist)
            except QueryRejected as e:
                if e.denied:  # Never retry around the deny list.
                    answer.error, answer.blocked_tables = str(e), e.denied
                    return answer
                feedback.append((translation.sql, str(e)))
                answer.error = str(e)
                continue

            # 2. Ask Postgres which tables it would actually scan (sees through views).
            try:
                relations = planned_relations(self.conn, checked.sql, self.timeout_ms)
            except psycopg.Error as e:
                feedback.append((checked.sql, _pg_message(e)))
                answer.error = _pg_message(e)
                continue
            denied = sorted(f"{s}.{t}" for s, t in relations if self.denylist.is_denied(t, s))
            if denied:
                answer.error = (
                    "Query would read table(s) on the do-not-query list (via a view or "
                    "subquery): " + ", ".join(denied)
                )
                answer.blocked_tables = denied
                return answer

            # 3. Execute in a read-only transaction.
            try:
                answer.result = run_readonly(self.conn, checked.sql, self.max_rows, self.timeout_ms)
                answer.sql, answer.error = checked.sql, None
                return answer
            except psycopg.Error as e:
                feedback.append((checked.sql, _pg_message(e)))
                answer.error = _pg_message(e)

        return answer


def _pg_message(e: psycopg.Error) -> str:
    diag = getattr(e, "diag", None)
    return (diag.message_primary if diag and diag.message_primary else str(e)).strip()
