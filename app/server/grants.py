"""GRANT statements the app service principal needs for the assessment.

Mirrors scripts/setup_app_permissions.py and notebooks/install.py. Pure: it builds
the SQL text only. An admin runs it once, in the SQL editor, with the app's
service principal id (shown by GET /api/setup/grants).
"""

SP_PLACEHOLDER = "<app-service-principal-id>"
SYSTEM_SCHEMAS = ("information_schema", "access", "query")


def _ident(name):
    """Back-quote a SQL identifier, doubling any embedded backtick."""
    return "`" + str(name).replace("`", "``") + "`"


def grant_statements(sp_id, catalogs=()):
    """The GRANT statements for the service principal `sp_id` (or the placeholder)."""
    sp = _ident(sp_id or SP_PLACEHOLDER)
    statements = [f"GRANT USE CATALOG ON CATALOG system TO {sp}"]
    for schema in SYSTEM_SCHEMAS:
        statements.append(f"GRANT USE SCHEMA ON SCHEMA system.{schema} TO {sp}")
        statements.append(f"GRANT SELECT ON SCHEMA system.{schema} TO {sp}")
    for catalog in catalogs:
        cat = _ident(catalog)
        statements.append(f"GRANT USE CATALOG ON CATALOG {cat} TO {sp}")
        statements.append(f"GRANT USE SCHEMA ON CATALOG {cat} TO {sp}")
        statements.append(f"GRANT SELECT ON CATALOG {cat} TO {sp}")
    return statements
