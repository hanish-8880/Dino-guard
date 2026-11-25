"""Database statements."""
from __future__ import annotations

import re

from ...engine.decisions import allow, deny, instruct
from ...engine.registry import builtin_policy
from . import _shell
from ._common import bash_cmd


def _sql_text(ctx) -> str:
    if ctx.tool_name == "Bash":
        return bash_cmd(ctx)
    if (ctx.tool_name or "").startswith("MCP:"):
        return _shell.text_of_input(ctx.tool_input)
    return ""


_DESTRUCTIVE = re.compile(r"(?i)\b(?:DROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW|COLLECTION)|TRUNCATE(?:\s+TABLE)?\s+[\w\"`\[])")
_MONGO_DROP = re.compile(r"\.(?:drop|dropDatabase)\(\s*\)")


@builtin_policy("warn-destructive-sql", category="database",
                description="Instruct caution on DROP / TRUNCATE statements.")
def warn_destructive_sql(ctx):
    text = _sql_text(ctx)
    if text and (_DESTRUCTIVE.search(text) or _MONGO_DROP.search(text)):
        return instruct("This statement drops or truncates data and cannot be undone. Confirm the target is a "
                        "local/dev database, take a backup (or wrap it in a transaction), and tell the user "
                        "exactly what will be removed.")
    return allow()


_DELETE = re.compile(r"(?is)\bDELETE\s+FROM\s+([\w.\"`\[\]]+)(.*?)(?:;|$|\"|')")


@builtin_policy("no-delete-without-where", category="database", default_enabled=False,
                description="Deny DELETE FROM statements with no WHERE clause.")
def no_delete_without_where(ctx):
    text = _sql_text(ctx)
    for m in _DELETE.finditer(text or ""):
        if not re.search(r"(?i)\bWHERE\b", m.group(2)):
            return deny(f"`DELETE FROM {m.group(1)}` without WHERE deletes every row. Add a WHERE clause that "
                        "selects only the rows you mean, and run the same condition as a SELECT first.")
    return allow()


_ALTER = re.compile(r"(?i)\bALTER\s+TABLE\b")
_MIGRATE = re.compile(r"(?i)\b(?:alembic\s+(?:upgrade|downgrade)|prisma\s+migrate\s+(?:deploy|reset)|prisma\s+db\s+push"
                      r"|rails\s+db:(?:migrate|rollback)|rake\s+db:migrate|manage\.py\s+migrate|knex\s+migrate"
                      r"|flyway\s+migrate|sequelize\s+db:migrate|diesel\s+migration\s+run|goose\s+up|dbmate\s+up"
                      r"|liquibase\s+update)\b")
_PROD = re.compile(r"(?i)(\bprod(?:uction)?\b|[-_.]prod[-_.]|rds\.amazonaws\.com|\.supabase\.co|planetscale|neon\.tech)")


@builtin_policy("warn-schema-change", category="database", default_enabled=False, authority="reviewable",
                reviewed_by=["on-task"], description="Instruct caution on ALTER TABLE and migrations against production.")
def warn_schema_change(ctx):
    text = _sql_text(ctx)
    if not text:
        return allow()
    if _ALTER.search(text):
        return instruct("This changes a table schema. Prefer a migration file in the repo, check it is reversible, "
                        "and confirm with the user which database it targets.")
    if _MIGRATE.search(text) and _PROD.search(text):
        return instruct("This runs migrations against what looks like a production database. Stop and confirm "
                        "with the user; run against a local or staging database first.")
    return allow()
