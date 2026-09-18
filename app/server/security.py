"""Security primitives shared by the app: log redaction, response hardening,
request-size limits, error sanitization, SQL identifier quoting, and the
authenticated-principal resolution used to scope per-user history.

The app reads a customer's own workspace, so the rules below assume its metadata
and query results may be sensitive:

* nothing derived from a request or an upstream response may reach the client
  verbatim (CWE-209) — callers raise freely, the boundary sanitizes;
* nothing that looks like a credential or a direct identifier may reach the log
  stream (CWE-532);
* per-user records are keyed on an identity the app has actually established,
  never on an unauthenticated request header alone (CWE-290/639).
"""

import hashlib
import html
import logging
import os
import re
import time
import uuid
from html.parser import HTMLParser
from typing import Optional

import aiohttp

from server._telemetry import with_ua

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Log redaction
# ---------------------------------------------------------------------------
# Applied to every record on the root logger. Upstream error text (SQL Warehouse
# messages, Genie/REST bodies) is echoed into logs by design — it is the only way
# to debug a customer deployment — but it can carry a bearer token or a value
# selected out of a sensitive column. Redact those shapes before they are written.
#
# Deliberately narrow: credentials, plus the direct personal identifiers (email,
# SSN, payment card). Broad numeric masking would corrupt the row counts and
# scores this app logs for diagnostics.
_REDACTIONS: tuple[tuple[re.Pattern, str], ...] = (
    # Databricks PATs / OAuth tokens.
    (re.compile(r"\bdapi[0-9a-fA-F]{32}\b"), "dapi[REDACTED]"),
    (re.compile(r"\bdkea[0-9a-fA-F]{16,}\b"), "dkea[REDACTED]"),
    # JWTs (the forwarded OBO token and Lakebase credentials are both JWT-shaped).
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"), "[REDACTED_JWT]"),
    # Any Authorization header value that made it into a message.
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    # `"token": "..."` / `password=...` in a serialized upstream payload.
    (re.compile(r"(?i)([\"']?(?:token|password|secret|client_secret|api_key)[\"']?\s*[:=]\s*[\"']?)"
                r"[^\s,;\"'}\]]{6,}"), r"\1[REDACTED]"),
    # US SSN.
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    # Payment card (13-19 digits, optionally space/hyphen grouped).
    (re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), "[REDACTED_PAN]"),
    # Email addresses -> keep the domain (useful for support), mask the local part.
    (re.compile(r"\b[A-Za-z0-9._%+-]+(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"), r"[REDACTED]\1"),
)


def redact(text: str) -> str:
    """Scrub credential- and identifier-shaped substrings out of ``text``."""
    if not text:
        return text
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Redact the formatted message (and any exception text) of every record.

    Installed on the root logger, so it covers this app, uvicorn, aiohttp, and
    the Databricks SDK without each of them having to opt in.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
            if record.exc_info:
                # Render the traceback now so its frames can be redacted too;
                # formatters emit `exc_text` verbatim when it is already set.
                if not record.exc_text:
                    record.exc_text = logging.Formatter().formatException(record.exc_info)
                record.exc_text = redact(record.exc_text)
                record.exc_info = None
        except Exception:  # never let logging hardening break logging
            pass
        return True


def install_log_redaction() -> None:
    """Attach the redaction filter to the root logger and every existing handler.

    A logger's filters run only for records logged directly to it, not for records
    that propagate up from children. uvicorn's loggers default to propagate=False
    with their own handlers, so a root-only filter never sees their access/error
    lines (CWE-532). Attach the filter to those loggers' handlers too."""
    root = logging.getLogger()
    if any(isinstance(f, RedactingFilter) for f in root.filters):
        return
    f = RedactingFilter()
    root.addFilter(f)
    for handler in root.handlers:
        handler.addFilter(f)
    # uvicorn (and its access/error children) carry their own non-propagating
    # handlers, so cover them explicitly.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "gunicorn.error"):
        lg = logging.getLogger(name)
        lg.addFilter(f)
        for handler in lg.handlers:
            handler.addFilter(f)


# ---------------------------------------------------------------------------
# Error sanitization
# ---------------------------------------------------------------------------
_GENERIC_ERROR = "The request could not be completed. Quote the reference below to support."


def safe_error(exc: BaseException, context: str, log: Optional[logging.Logger] = None) -> tuple[str, str]:
    """Log ``exc`` in full and return ``(reference_id, client_safe_message)``.

    Upstream failures routinely echo the statement that failed, the object names
    involved, and sometimes a literal value from a column — none of which may be
    returned to the browser. The caller shows the message; an operator correlates
    it to the full traceback in the app log via the reference id.
    """
    reference = uuid.uuid4().hex[:12]
    (log or logger).error("[%s] %s: %s", reference, context, exc, exc_info=True)
    return reference, f"{_GENERIC_ERROR} (reference {reference})"


# ---------------------------------------------------------------------------
# SQL identifier quoting
# ---------------------------------------------------------------------------
def quote_ident(name: str) -> str:
    """Backtick-quote a Unity Catalog identifier for interpolation into SQL.

    Catalog and schema names are enumerated from the metastore, so they are not
    literals the app controls: a name containing a backtick would otherwise close
    the quoting early and let the rest of the name be parsed as SQL (CWE-89).
    Databricks escapes a backtick inside a quoted identifier by doubling it.
    """
    if not isinstance(name, str) or not name:
        raise ValueError("SQL identifier must be a non-empty string")
    if "\x00" in name or "\n" in name or "\r" in name:
        raise ValueError("SQL identifier contains a control character")
    return "`" + name.replace("`", "``") + "`"


def quote_literal(value: str) -> str:
    """Single-quote a string literal for a SQL ``IN (...)`` list / COMMENT etc.

    Spark SQL treats backslash as an escape inside string literals by default, so a
    value ending in ``\\`` would otherwise escape the closing quote and let the rest
    parse as SQL (CWE-89). Double backslashes first, then single quotes."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# PDF/HTML escaping
# ---------------------------------------------------------------------------
def escape_html(text: str) -> str:
    """Escape text destined for an HTML document (including attribute context)."""
    return html.escape(text or "", quote=True)


# python-markdown has no safe mode: raw HTML in the source document is emitted
# verbatim. The plan Markdown is model-generated and round-trips through the
# browser, so the rendered HTML is reduced to the tags this app's PDF template
# actually styles before any engine parses it.
#
# Tag set = what the enabled markdown extensions (tables, fenced_code, toc,
# sane_lists) produce. Everything else is dropped. Resource-loading elements
# (img/link/object/embed/iframe) are absent on purpose — they are the vector the
# renderer's link_callback also refuses.
_ALLOWED_TAGS: dict[str, frozenset] = {
    "h1": frozenset({"id"}), "h2": frozenset({"id"}), "h3": frozenset({"id"}),
    "h4": frozenset({"id"}), "h5": frozenset({"id"}), "h6": frozenset({"id"}),
    "p": frozenset(), "br": frozenset(), "hr": frozenset(),
    "strong": frozenset(), "b": frozenset(), "em": frozenset(), "i": frozenset(),
    "ul": frozenset(), "ol": frozenset(), "li": frozenset(),
    "blockquote": frozenset(),
    "code": frozenset({"class"}), "pre": frozenset({"class"}),
    "table": frozenset(), "thead": frozenset(), "tbody": frozenset(), "tr": frozenset(),
    "th": frozenset({"colspan", "rowspan", "align"}),
    "td": frozenset({"colspan", "rowspan", "align"}),
    "div": frozenset({"class"}), "span": frozenset({"class"}),
    "a": frozenset({"href"}),
}
_VOID_TAGS = frozenset({"br", "hr"})
# Tags whose *content* is dropped along with the tag; leaving the text of a
# <script> or <style> body inline would resurface it as document text.
_DROP_CONTENT_TAGS = frozenset({"script", "style", "head", "title", "template"})
_SAFE_URL_SCHEMES = ("http://", "https://", "mailto:")


class _HtmlSanitizer(HTMLParser):
    """Reduce a fragment to `_ALLOWED_TAGS`, keeping the text of dropped tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._open: list[str] = []
        self._suppress_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_CONTENT_TAGS:
            self._suppress_depth += 1
            return
        if self._suppress_depth or tag not in _ALLOWED_TAGS:
            return
        allowed = _ALLOWED_TAGS[tag]
        rendered = []
        for name, value in attrs:
            if name not in allowed or value is None:
                continue
            if name == "href" and not value.lower().startswith(_SAFE_URL_SCHEMES):
                continue
            rendered.append(f' {name}="{html.escape(value, quote=True)}"')
        if tag in _VOID_TAGS:
            self._out.append(f"<{tag}{''.join(rendered)} />")
        else:
            self._out.append(f"<{tag}{''.join(rendered)}>")
            self._open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID_TAGS or tag in _ALLOWED_TAGS:
            self.handle_starttag(tag, attrs)
            if tag not in _VOID_TAGS and self._open and self._open[-1] == tag:
                self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _DROP_CONTENT_TAGS:
            self._suppress_depth = max(0, self._suppress_depth - 1)
            return
        if self._suppress_depth or tag in _VOID_TAGS or tag not in _ALLOWED_TAGS:
            return
        if tag in self._open:
            # Close anything the document left open inside this tag, so a stray
            # unclosed element cannot swallow the rest of the fragment.
            while self._open:
                open_tag = self._open.pop()
                self._out.append(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data):
        if not self._suppress_depth:
            self._out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        pass

    def unknown_decl(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def handle_pi(self, data):
        pass

    def result(self) -> str:
        while self._open:
            self._out.append(f"</{self._open.pop()}>")
        return "".join(self._out)


def sanitize_html_fragment(fragment: str) -> str:
    """Strip a rendered-Markdown fragment down to the allowlisted tag set."""
    parser = _HtmlSanitizer()
    parser.feed(fragment or "")
    parser.close()
    return parser.result()


# ---------------------------------------------------------------------------
# Authenticated principal
# ---------------------------------------------------------------------------
# Databricks Apps authenticates the viewer at its proxy and forwards the result
# as `X-Forwarded-Email` (always) and `X-Forwarded-Access-Token` (only when user
# authorization is enabled). The proxy strips inbound copies of both, so in a
# supported deployment the header is trustworthy — but the app must not *depend*
# on that, because a header is all an attacker with direct network access to the
# container would need to read another user's assessments and plans.
#
# So: when a forwarded token is present, resolve the identity FROM THE TOKEN
# (the workspace tells us who it belongs to) and use that as the ownership key.
# The header is the fallback, and the lookup is best-effort with a short timeout
# so an API blip degrades to the previous behaviour instead of failing the app.
_IDENTITY_TTL = 600  # seconds; a token outlives this many times over
# A failed lookup is cached too, for a shorter window. Without this, a workspace
# whose SCIM endpoint is slow or unreachable would make EVERY per-user request pay
# the full timeout before falling back to the header — turning a degraded
# dependency into an app-wide latency regression.
_IDENTITY_NEGATIVE_TTL = 60
_IDENTITY_LOOKUP_TIMEOUT = 5
_identity_cache: dict[str, tuple[float, Optional[str]]] = {}
_IDENTITY_CACHE_MAX = 500

# Ownership key used when the app runs outside Databricks Apps (local development
# against a CLI profile).
LOCAL_PRINCIPAL = "local-dev"

# Ownership key used when a deployed app receives no identity at all. This should
# not happen — the platform proxy always forwards one — but if it does, the app
# keeps working and history stays reachable rather than locking the user out. It
# is a named bucket rather than NULL so it is visible in the data and greppable in
# the logs; behaviour matches what the NULL key already did.
UNATTRIBUTED_PRINCIPAL = "unattributed"


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _remember_identity(fingerprint: str, identity: Optional[str]) -> None:
    """Cache a lookup outcome, evicting the oldest entry when the cache is full."""
    if len(_identity_cache) >= _IDENTITY_CACHE_MAX:
        oldest = min(_identity_cache, key=lambda k: _identity_cache[k][0])
        del _identity_cache[oldest]
    _identity_cache[fingerprint] = (time.time(), identity)


async def _identity_from_token(token: str) -> Optional[str]:
    """The workspace's own answer to 'who does this token belong to', cached."""
    fingerprint = _token_fingerprint(token)
    hit = _identity_cache.get(fingerprint)
    if hit is not None:
        age = time.time() - hit[0]
        # Negative entries expire sooner, so a transient failure self-heals quickly
        # while a persistent one stays cheap.
        if age < (_IDENTITY_TTL if hit[1] else _IDENTITY_NEGATIVE_TTL):
            return hit[1]

    from server.config import get_workspace_host

    host = get_workspace_host()
    if not host:
        _remember_identity(fingerprint, None)
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_IDENTITY_LOOKUP_TIMEOUT), headers=with_ua()) as session:
            async with session.get(
                f"{host}/api/2.0/preview/scim/v2/Me",
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                if response.status != 200:
                    logger.warning("identity lookup returned %s; falling back to the forwarded header",
                                   response.status)
                    _remember_identity(fingerprint, None)
                    return None
                data = await response.json()
    except Exception as e:
        logger.warning(f"identity lookup failed ({type(e).__name__}); falling back to the forwarded header")
        _remember_identity(fingerprint, None)
        return None

    identity = (data.get("userName") or "").strip().lower()
    if not identity:
        emails = data.get("emails") or []
        identity = next((e.get("value", "") for e in emails if e.get("primary")), "").strip().lower()
    if not identity:
        _remember_identity(fingerprint, None)
        return None

    _remember_identity(fingerprint, identity)
    return identity


async def resolve_principal(
    forwarded_email: Optional[str],
    forwarded_token: Optional[str],
) -> Optional[str]:
    """The ownership key for this request's per-user records.

    Always returns a usable key — a request is never refused for lack of an
    identity, because that would make a user's own history unreachable over an
    infrastructure hiccup. Preference order: the identity the workspace confirms
    for a forwarded token, then the forwarded header, then a named fallback.
    """
    if forwarded_token:
        verified = await _identity_from_token(forwarded_token)
        if verified:
            return verified

    if forwarded_email:
        normalized = forwarded_email.strip().lower()
        if normalized:
            return normalized
        # A whitespace-only header is not a usable identity — fall through to the
        # named bucket rather than returning None (which would pool such requests
        # under a NULL created_by, violating the always-usable-key contract).

    from server.config import IS_DATABRICKS_APP

    if not IS_DATABRICKS_APP:
        return LOCAL_PRINCIPAL

    # Deployed, yet the proxy forwarded no identity at all. Serve the request from
    # a named shared bucket rather than failing it, and make the anomaly loud.
    logger.warning(
        "no forwarded identity on a deployed request; serving per-user history from "
        "the '%s' bucket. Check that user authorization is configured for this app.",
        UNATTRIBUTED_PRINCIPAL,
    )
    return UNATTRIBUTED_PRINCIPAL


# ---------------------------------------------------------------------------
# Response hardening
# ---------------------------------------------------------------------------
# Workspace domains that are allowed to frame the app. Databricks Apps are opened
# on their own origin but can be embedded by the workspace UI, so a bare
# `frame-ancestors 'none'` would break that embed. Overridable for customers who
# front the app with their own domain.
_DEFAULT_FRAME_ANCESTORS = (
    "'self' https://*.databricks.com https://*.azuredatabricks.net "
    "https://*.gcp.databricks.com https://*.databricksapps.com"
)

FRAME_ANCESTORS = os.environ.get("APP_FRAME_ANCESTORS", "").strip() or _DEFAULT_FRAME_ANCESTORS

# script-src stays 'self': Vite emits the app as external, content-hashed bundles
# and index.html carries no inline script. style-src needs 'unsafe-inline'
# because React and Recharts set element styles inline at runtime. blob: is
# allowed for object/frame only, so the generated plan PDF still opens.
_CSP = "; ".join([
    "default-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'self' blob:",
    "frame-src 'self' blob:",
    "worker-src 'self' blob:",
    f"frame-ancestors {FRAME_ANCESTORS}",
    "upgrade-insecure-requests",
])

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    # frame-ancestors above is authoritative in every browser that supports CSP2;
    # this covers the rest without contradicting the workspace-embed allowance.
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                          "magnetometer=(), microphone=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


# Pre-encoded once at import. Encoding these per response showed up as measurable
# per-request overhead, and the values never change at runtime.
_SECURITY_HEADERS_ENCODED: tuple[tuple[bytes, bytes], ...] = tuple(
    (name.lower().encode("latin-1"), value.encode("latin-1"))
    for name, value in _SECURITY_HEADERS.items()
)
_SECURITY_HEADER_NAMES: frozenset[bytes] = frozenset(n for n, _ in _SECURITY_HEADERS_ENCODED)


class SecurityHeadersMiddleware:
    """Attach the hardening headers to every response (pure ASGI, so it also
    covers streaming SSE responses and static files)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                # Only scan for collisions if the response actually set one of
                # ours; the common case appends without any comparison at all.
                if any(k in _SECURITY_HEADER_NAMES for k, _ in headers):
                    existing = {k for k, _ in headers}
                    headers.extend(
                        (n, v) for n, v in _SECURITY_HEADERS_ENCODED if n not in existing
                    )
                else:
                    headers.extend(_SECURITY_HEADERS_ENCODED)
            await send(message)

        await self.app(scope, receive, send_with_headers)



class RequestTooLarge(Exception):
    """Raised by the body-size limiter once the cap is exceeded."""


class BodySizeLimitMiddleware:
    """Reject request bodies over ``max_bytes`` (CWE-770).

    The plan endpoints accept model-generated Markdown of unbounded length, which
    is then rendered to PDF and persisted — an easy way to exhaust memory or fill
    the history table. Enforced on the declared Content-Length and again while
    streaming, so a chunked body cannot slip past.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    _TOO_LARGE = b'{"error":"Request body is too large."}'

    async def _reject(self, send):
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(self._TOO_LARGE)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": self._TOO_LARGE})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

        # Scan for content-length directly rather than materialising every header
        # into a dict on each request.
        for key, value in scope.get("headers") or ():
            if key == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        await self._reject(send)
                        return
                except ValueError:
                    pass
                break

        received = 0

        async def receive_capped():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestTooLarge()
            return message

        try:
            await self.app(scope, receive_capped, send)
        except RequestTooLarge:
            await self._reject(send)
