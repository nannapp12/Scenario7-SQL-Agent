"""Natural language -> SQL translation with Claude."""

from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You translate questions about a PostgreSQL database into a single SQL query.

Rules:
- Write exactly one read-only SELECT statement (CTEs are fine) in PostgreSQL dialect.
- Use only the tables and columns in the schema below. Schema-qualify table names.
- If the question asks for a large or unbounded list, add a sensible LIMIT.
- If the question can't be answered from this schema, set "sql" to an empty string and
  explain why in "explanation". Do not guess at tables that aren't listed.

Schema (one table per line, as schema.table(column type, ...)):
{schema}"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string", "description": "The SQL query, or empty if unanswerable."},
        "explanation": {"type": "string", "description": "One or two sentences on the approach."},
    },
    "required": ["sql", "explanation"],
    "additionalProperties": False,
}


@dataclass
class Translation:
    sql: str
    explanation: str


class TranslationError(Exception):
    pass


class Translator:
    def __init__(self, schema_text: str, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.system = SYSTEM_PROMPT.format(schema=schema_text or "(no queryable tables)")

    def translate(self, question: str, feedback: list[tuple[str, str]] | None = None) -> Translation:
        """Translate ``question``. ``feedback`` holds (previous_sql, error) pairs from failed attempts."""
        content = question
        for prev_sql, error in feedback or []:
            content += (
                f"\n\nA previous attempt failed.\nSQL:\n{prev_sql}\nError:\n{error}\n"
                "Write a corrected query."
            )

        response = self.client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            # The schema is the large, stable part of the prompt; cache it across questions.
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
        )

        if response.stop_reason == "refusal":
            raise TranslationError("The model declined to answer this question.")
        if response.stop_reason == "max_tokens":
            raise TranslationError("The model's response was cut off; try a narrower question.")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise TranslationError("The model returned no SQL.")
        data = json.loads(text)
        return Translation(sql=data["sql"].strip(), explanation=data["explanation"].strip())
