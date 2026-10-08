# SQL Agent

Ask questions about your PostgreSQL data in plain English. The agent:

1. **Translates** your question into a SQL query with Claude, using the database schema
   (tables on the do-not-query list are left out of the schema Claude sees).
2. **Checks** the query before it runs:
   - it must be a single read-only `SELECT` (no `INSERT`/`UPDATE`/`DELETE`/DDL, no `SELECT INTO`,
     no file-reading or server-admin functions);
   - it must not reference any table in [`do_not_query.txt`](do_not_query.txt), including in
     subqueries, CTEs, `UNION`s and joins (checked by parsing the SQL with `sqlglot`);
   - Postgres's own query plan (`EXPLAIN`, which doesn't run the query) must not touch a
     deny-listed table. This catches views built on top of blocked tables.
3. **Runs** the query in a `READ ONLY` transaction with a statement timeout and row limit,
   then prints the results as a table.

If a query is rejected for being invalid, or Postgres returns an error (e.g. a wrong column
name), the error goes back to Claude for a corrected query (up to 3 attempts). A deny-list hit
is never retried. It stops right away and reports which table was blocked.

## Setup in VS Code

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit .env
```

In `.env`, set:

- `ANTHROPIC_API_KEY`: your Anthropic API key
- `DATABASE_URL`: e.g. `postgresql://readonly_user:password@localhost:5432/mydb`

Edit `do_not_query.txt` to list the tables the agent must never read:

```
salaries            # blocked in every schema
public.payroll      # blocked in the public schema only
audit.*             # every table in the audit schema
```

In VS Code, select the `.venv` interpreter (**Python: Select Interpreter**), then open **Run and Debug**:

- **SQL Agent (interactive)**: starts a prompt in the terminal where you can ask questions one after another.
- **SQL Agent (one question)**: asks you for one question, answers it, and exits.

You can also run it from the terminal:

```bash
python -m sql_agent                                  # interactive
python -m sql_agent "top 5 customers by revenue in 2025"
python -m sql_agent --hide-sql --max-rows 50 "..."
```

Example:

```
> how many orders did each region place last month?

SQL:
  SELECT r.name, count(*) AS orders FROM public.orders o JOIN public.regions r ON ...

Counts orders per region for the previous calendar month.

+---------+----------+
| name    |   orders |
|---------+----------|
| East    |      412 |
| West    |      388 |
+---------+----------+
2 row(s)
```

## Configuration

| Setting | Env var | CLI flag | Default |
|---|---|---|---|
| Database URL | `DATABASE_URL` | `--database-url` | (required) |
| Deny list path | `DENYLIST_PATH` | `--denylist` | `do_not_query.txt` |
| Max rows shown | `MAX_ROWS` | `--max-rows` | 200 |
| Statement timeout | `STATEMENT_TIMEOUT_MS` | `--timeout-ms` | 15000 |

The model is set in `sql_agent/translator.py` (`MODEL`).

## Use a read-only database user

The agent's checks are strict, but the strongest guarantee comes from Postgres permissions.
Connect with a role that can only read the tables you allow:

```sql
CREATE ROLE sql_agent LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE mydb TO sql_agent;
GRANT USAGE ON SCHEMA public TO sql_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sql_agent;
REVOKE SELECT ON public.salaries, public.payroll FROM sql_agent;  -- mirror do_not_query.txt
```

## Tests

```bash
pytest                                                       # SQL guard unit tests
TEST_DATABASE_URL=postgresql://... pytest                    # plus integration tests
```

The integration tests create and drop `employees`, `salaries` and `pay_view` in the target
database, so point `TEST_DATABASE_URL` at a scratch database. They use a fake translator, so
no API key is needed.

## Project layout

```
sql_agent/
  translator.py  question -> SQL with Claude (structured JSON output)
  guard.py       parses the SQL: read-only, single statement, deny-list check
  db.py          schema introspection, EXPLAIN-based table check, read-only execution
  denylist.py    loads and matches do_not_query.txt
  agent.py       ties it together, with retries
  cli.py         terminal interface
```
