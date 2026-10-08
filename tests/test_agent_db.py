"""Integration tests against a real Postgres. Set TEST_DATABASE_URL to run them.

The translator is faked, so no Anthropic API key is needed.
"""

import os

import psycopg
import pytest

from sql_agent.agent import SQLAgent
from sql_agent.db import describe_schema
from sql_agent.denylist import DenyList
from sql_agent.translator import Translation

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

DENY = DenyList.from_entries(["salaries"])


class FakeTranslator:
    def __init__(self, *sqls):
        self.sqls = list(sqls)
        self.calls = []

    def translate(self, question, feedback=None):
        self.calls.append(list(feedback or []))
        return Translation(sql=self.sqls.pop(0), explanation="fake")


@pytest.fixture
def conn():
    with psycopg.connect(URL) as c:
        c.execute("DROP VIEW IF EXISTS pay_view; DROP TABLE IF EXISTS employees, salaries")
        c.execute("CREATE TABLE employees (id int primary key, name text)")
        c.execute("CREATE TABLE salaries (employee_id int, amount int)")
        c.execute("CREATE VIEW pay_view AS SELECT e.name, s.amount FROM employees e JOIN salaries s ON s.employee_id = e.id")
        c.execute("INSERT INTO employees VALUES (1, 'Ada'), (2, 'Grace'), (3, 'Linus')")
        c.execute("INSERT INTO salaries VALUES (1, 100)")
        c.commit()
        yield c
        c.rollback()
        c.execute("DROP VIEW IF EXISTS pay_view; DROP TABLE IF EXISTS employees, salaries")
        c.commit()


def agent(conn, *sqls, **kw):
    return SQLAgent(conn, DENY, translator=FakeTranslator(*sqls), **kw)


def test_schema_hides_denied_tables(conn):
    schema = describe_schema(conn, DENY)
    assert "public.employees(" in schema
    assert "public.salaries(" not in schema


def test_runs_query_and_returns_rows(conn):
    ans = agent(conn, "SELECT name FROM employees ORDER BY id").ask("names?")
    assert ans.error is None
    assert ans.result.columns == ["name"]
    assert ans.result.rows == [("Ada",), ("Grace",), ("Linus",)]


def test_truncates_to_max_rows(conn):
    ans = agent(conn, "SELECT name FROM employees ORDER BY id", max_rows=2).ask("names?")
    assert ans.result.rows == [("Ada",), ("Grace",)] and ans.result.truncated


def test_blocks_denied_table_without_retry(conn):
    a = agent(conn, "SELECT * FROM salaries", "SELECT 1")
    ans = a.ask("salaries?")
    assert ans.blocked_tables == ["public.salaries"] and ans.result is None
    assert len(a.translator.calls) == 1


def test_blocks_denied_table_behind_view(conn):
    ans = agent(conn, "SELECT * FROM pay_view").ask("pay?")
    assert ans.blocked_tables == ["public.salaries"] and ans.result is None


def test_retries_with_db_error_feedback(conn):
    a = agent(conn, "SELECT nme FROM employees", "SELECT count(*) FROM employees")
    ans = a.ask("how many?")
    assert ans.error is None and ans.result.rows == [(3,)]
    assert "nme" in a.translator.calls[1][0][1]


def test_unanswerable_question(conn):
    ans = agent(conn, "").ask("weather?")
    assert ans.error == "fake" and ans.result is None
