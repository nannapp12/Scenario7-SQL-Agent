"""Command-line entry point: ``python -m sql_agent "your question"`` or interactive mode."""

from __future__ import annotations

import argparse
import os
import sys

import anthropic
import psycopg
from dotenv import load_dotenv
from tabulate import tabulate

from .agent import AgentAnswer, SQLAgent
from .denylist import DenyList
from .translator import TranslationError


def print_answer(answer: AgentAnswer, show_sql: bool = True) -> None:
    if show_sql and answer.sql:
        print(f"\nSQL:\n  {answer.sql.replace(chr(10), chr(10) + '  ')}")
    if answer.explanation:
        print(f"\n{answer.explanation}")
    if answer.error:
        label = "BLOCKED" if answer.blocked_tables else "ERROR"
        print(f"\n{label}: {answer.error}")
        return
    result = answer.result
    if result is None:
        return
    if not result.rows:
        print("\n(no rows)")
        return
    print()
    print(tabulate(result.rows, headers=result.columns, tablefmt="psql"))
    suffix = f" (truncated to first {len(result.rows)})" if result.truncated else ""
    print(f"{len(result.rows)} row(s){suffix}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Ask questions about your PostgreSQL data in plain English.")
    parser.add_argument("question", nargs="*", help="Question to ask. Omit for interactive mode.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="Postgres URL (default: $DATABASE_URL)")
    parser.add_argument("--denylist", default=os.getenv("DENYLIST_PATH", "do_not_query.txt"), help="Path to the do-not-query list")
    parser.add_argument("--max-rows", type=int, default=int(os.getenv("MAX_ROWS", "200")))
    parser.add_argument("--timeout-ms", type=int, default=int(os.getenv("STATEMENT_TIMEOUT_MS", "15000")))
    parser.add_argument("--hide-sql", action="store_true", help="Don't print the generated SQL")
    args = parser.parse_args(argv)

    if not args.database_url:
        parser.error("Set DATABASE_URL (in .env or the environment) or pass --database-url.")

    try:
        denylist = DenyList.from_file(args.denylist)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2

    with psycopg.connect(args.database_url) as conn:
        agent = SQLAgent(conn, denylist, max_rows=args.max_rows, timeout_ms=args.timeout_ms)

        def handle(question: str) -> None:
            try:
                print_answer(agent.ask(question), show_sql=not args.hide_sql)
            except TranslationError as e:
                print(f"\nERROR: {e}")
            except anthropic.AuthenticationError:
                print("\nERROR: Anthropic credentials are missing or invalid. Set ANTHROPIC_API_KEY.")
            except anthropic.RateLimitError:
                print("\nERROR: Rate limited by the Anthropic API. Wait a moment and try again.")
            except anthropic.APIStatusError as e:
                print(f"\nERROR: Anthropic API error ({e.status_code}): {e.message}")
            except anthropic.APIConnectionError:
                print("\nERROR: Couldn't reach the Anthropic API. Check your network.")

        if args.question:
            handle(" ".join(args.question))
            return 0

        print("SQL agent ready. Ask a question about your data (Ctrl+D or 'exit' to quit).")
        while True:
            try:
                question = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if question.lower() in {"exit", "quit"}:
                return 0
            if question:
                handle(question)


if __name__ == "__main__":
    sys.exit(main())
