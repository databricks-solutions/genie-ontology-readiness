"""Outbound ``User-Agent`` for the app's Databricks API calls.

Builds the ``User-Agent`` header this app stamps on every Databricks API call it
makes (SQL Statement Execution, the Genie Conversation API, SCIM, serving
endpoints / Foundation Model API, Lakebase credential minting, catalog/domain
reads), so its requests are identified by a descriptive, versioned client token
instead of a generic HTTP-client default. This is standard client hygiene: a
well-behaved API client names and versions itself.

Each tab of the critical user journey identifies itself distinctly:

    genie-ontology-readiness-assess/<version>
    genie-ontology-readiness-plan/<version>
    genie-ontology-readiness-learn/<version>

Calls outside a tabbed request (app bootstrap ``/config``, background/unattended
runs, the SDK ``WorkspaceClient``) use the un-suffixed base token:

    genie-ontology-readiness/<version>

The active phase is held in a contextvar set once per request by a router-level
dependency (see ``routes/__init__.py``), mirroring how the OBO user token is
scoped per request. Because the app never hands API work to threads/executors,
the contextvar is visible to every aiohttp session created while serving the
request, so a shared service module (``sql_client`` etc.) reports the phase of
whichever tab invoked it.
"""

import contextvars
import os
import pathlib
import re
import subprocess

PRODUCT_NAME = "genie-ontology-readiness"

# Last-resort version when neither the deploy-baked env nor git is available.
_FALLBACK_VERSION = "0.0.0+unknown"

# Env var carrying the version into the deployed Databricks App, where there is
# no .git (the bundle syncs only ./app). scripts/post_deploy.py bakes
# ``git describe`` into app.yml as this var at deploy time.
_VERSION_ENV = "GOR_VERSION"


def _git_describe() -> str | None:
    """Raw ``git describe`` (tag-derived), or None outside a tagged checkout.

    Deliberately no ``--always``: a checkout with no reachable tag should fail
    here and fall through to a semver-valid fallback, rather than returning a
    bare commit SHA — the Databricks SDK's user-agent layer validates
    ``product_version`` as semver and raises ValueError on a non-semver value.
    """
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--dirty"],
            cwd=pathlib.Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout.strip()
        return out or None
    except Exception:
        return None


def _normalize(version: str) -> str:
    """Drop a leading ``v`` from a tag (``v1.2.1`` -> ``1.2.1``), for semver."""
    return re.sub(r"^v(?=\d)", "", version.strip())


def git_version() -> str | None:
    """The normalized, tag-derived version, or None outside a tagged checkout.

    Shared with the deploy scripts (post_deploy.py, setup_app_permissions.py) so
    the product version is derived one way in one place — no divergent copies.
    """
    described = _git_describe()
    return _normalize(described) if described else None


def resolve_version() -> str:
    """Version for the product tag: ``GOR_VERSION`` env -> git tag -> fallback.

    Always returns a semver-valid string (the SDK rejects non-semver). Resolved
    once at import; the deployed app hits the env branch and never shells out.
    """
    env = os.environ.get(_VERSION_ENV, "").strip()
    if env:
        return _normalize(env)
    return git_version() or _FALLBACK_VERSION


PRODUCT_VERSION = resolve_version()

# CUJ phases that get their own product suffix. Anything else → base product.
VALID_PHASES = frozenset({"assess", "plan", "learn"})

# The current request's CUJ phase; ``None`` → base product (no suffix).
_product_phase: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_product_phase", default=None
)


def set_product_phase(phase: str | None) -> None:
    """Set the CUJ phase for the current request context.

    Unknown values fall back to ``None`` (base product) so a typo degrades to a
    valid, if less specific, token rather than emitting a bogus product name.
    """
    _product_phase.set(phase if phase in VALID_PHASES else None)


def product_name() -> str:
    """The phase-qualified product name for the current context."""
    phase = _product_phase.get()
    return f"{PRODUCT_NAME}-{phase}" if phase else PRODUCT_NAME


def user_agent() -> str:
    """The User-Agent value to stamp on outbound Databricks API calls."""
    return f"{product_name()}/{PRODUCT_VERSION}"


def with_ua(headers: dict | None = None) -> dict:
    """Return ``headers`` (copied) with the current-context User-Agent set.

    Single source of truth for attaching the product tag. Use as the ``headers``
    argument when creating an ephemeral ``aiohttp.ClientSession`` (evaluated in
    the request context, so it captures the right phase), or to enrich a
    per-request header dict for a long-lived/shared session that outlives any
    single request's phase.
    """
    merged = dict(headers) if headers else {}
    merged["User-Agent"] = user_agent()
    return merged
