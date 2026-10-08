import pytest

from sql_agent.denylist import DenyList
from sql_agent.guard import QueryRejected, check_query

DENY = DenyList.from_entries(["salaries", "hr.reviews", "secret.*", "# comment", ""])


def tables(sql):
    return check_query(sql, DENY).tables


def test_allows_simple_select():
    assert tables("SELECT * FROM public.orders") == ["public.orders"]


def test_collects_joined_and_subquery_tables():
    sql = """
        SELECT c.name, (SELECT count(*) FROM orders o WHERE o.customer_id = c.id)
        FROM customers c JOIN regions r ON r.id = c.region_id
    """
    assert set(tables(sql)) == {"public.customers", "public.regions", "public.orders"}


def test_cte_names_are_not_tables():
    assert tables("WITH big AS (SELECT * FROM orders) SELECT * FROM big") == ["public.orders"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM salaries",
        "SELECT * FROM public.SALARIES",
        'SELECT * FROM "salaries"',
        "SELECT * FROM payroll.salaries",  # bare name blocked in every schema
        "SELECT * FROM hr.reviews",
        "SELECT * FROM secret.anything",
        "SELECT * FROM orders WHERE id IN (SELECT id FROM salaries)",
        "WITH x AS (SELECT * FROM salaries) SELECT * FROM x",
        "SELECT * FROM orders UNION SELECT * FROM salaries",
        "SELECT * FROM orders o JOIN LATERAL (SELECT * FROM salaries) s ON true",
    ],
)
def test_blocks_denied_tables(sql):
    with pytest.raises(QueryRejected) as exc:
        check_query(sql, DENY)
    assert exc.value.denied


def test_schema_specific_entry_does_not_block_other_schemas():
    assert tables("SELECT * FROM public.reviews") == ["public.reviews"]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET total = 0",
        "INSERT INTO orders VALUES (1)",
        "DROP TABLE orders",
        "SELECT * INTO copy_of_orders FROM orders",
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
        "SELECT 1; DROP TABLE orders",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_sleep(100)",
        "COPY orders TO '/tmp/x'",
        "SET ROLE postgres",
        "not sql at all (",
    ],
)
def test_rejects_unsafe_or_invalid(sql):
    with pytest.raises(QueryRejected) as exc:
        check_query(sql, DENY)
    assert not exc.value.denied


def test_strips_trailing_semicolon():
    assert check_query("SELECT 1;", DENY).sql == "SELECT 1"
