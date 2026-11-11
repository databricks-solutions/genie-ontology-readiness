"""Where the app keeps its Lakebase history tables. Pure: no I/O, no SDK.

Existing installs wrote to the database's ``public`` schema. A new database has no
``CREATE`` on ``public`` for the app's service principal (Postgres 16), so a new install
uses its own schema instead. The choice is made once, at startup:

- the history tables already exist in ``public``: keep using ``public``, so the old
  history stays where it is;
- otherwise: create ``genie_ontology_readiness`` and keep the tables there.
"""

LEGACY_SCHEMA = "public"
HISTORY_SCHEMA = "genie_ontology_readiness"
HISTORY_TABLES = ("ontology_assessment_snapshots", "ontology_plans")


def choose_history_schema(existing_public_tables):
    """Return (schema, reason). `existing_public_tables` = history table names found in public."""
    found = sorted(set(existing_public_tables) & set(HISTORY_TABLES))
    if found:
        return LEGACY_SCHEMA, f"existing tables in public: {', '.join(found)}"
    return HISTORY_SCHEMA, "new database; the app creates its own schema"


def quote_ident(name):
    """A Postgres identifier, double-quoted (embedded quotes doubled)."""
    return '"' + str(name).replace('"', '""') + '"'


def qualified_table(schema, table):
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def legacy_probe_sql():
    """SQL that returns a row when a history table exists in public. Needs no privilege on it."""
    checks = " OR ".join(f"to_regclass('public.{t}') IS NOT NULL" for t in HISTORY_TABLES)
    return f"SELECT ({checks}) AS legacy_present"
