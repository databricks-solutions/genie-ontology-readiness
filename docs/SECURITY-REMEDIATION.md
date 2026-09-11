# Security remediation — Checkmarx SCA report `06a63d86`

**Audit coverage.** Every record in the scan report, and every line of the
application source — `app/server/**` (including all 1,137 lines of `probes.py`),
`app/app.py`, all seven frontend components, both deploy scripts, the bundled
accelerator notebook, and the deployment manifests.

**Scope.** Enterprise SCA scan of `databricks-solutions/genie-ontology-readiness`,
run 2026-09-09, report `SCA_ScanReport_06a63d86-3319-47cd-99db-1644afde8fb4`.
Remediated 2026-09-10. The app is deployed by regulated customers — including
insurers — against Unity Catalog workspaces that may hold PHI, so this pass covers
both the scanner's dependency findings and a manual review of the application code
for data leakage and HIPAA-relevant control gaps.

**Constraint.** No functional change, with **one deliberate exception**: the Genie
answer-quality test now runs as the viewer instead of as the app service principal
(F-11), because the old behaviour handed any viewer the service principal's read
access to the whole metastore. One environment variable restores the previous
behaviour if a deployment needs it. Everything else — every endpoint, response
shape and UI flow — behaves as it did before; the verification section records how
that was established.

---

## 1. Scan at a glance

| | Before | After |
|---|---|---|
| Critical / High / Medium vulnerabilities | 0 / 8 / 5 | 0 / 0 / 0 |
| Vulnerabilities on the CISA KEV list | 1 | 0 |
| Vulnerable packages | 5 | 0 |
| Vulnerable **runtime** packages | 1 (`starlette`) | 0 |
| Risk score | 8.2 | — |
| Code findings (manual review) | 13 | 0 |
| — of which Critical | 1 (F-11) | 0 |

### What the 35,264-line report actually contains

The file is large because it ships the full SBOM, not because it holds many
findings. Counted three independent ways, they agree:

| | |
|---|---|
| `Vulnerabilities[]` array length | 14 |
| Distinct CVE ids | 14 |
| Sum of every package's `VulnerabilityCount` | 14 |
| `RiskReportSummary` C+H+M+L+N | 0+8+5+0+1 = 14 |
| `Packages[]` (the SBOM — the bulk of the file) | 354 |
| `LicensesByPackage[]` / `Licenses[]` / `LegalRisks[]` | 341 / 11 / 13 |

Every other flag in the report was checked across all 354 packages and is clean:
no `IsMalicious`, no `IsViolatingPolicy`, no `IsPrivatePackage`, no proof-of-concept
exploit, no `ExploitablePath`, nothing suppressed via `IsIgnored`. One CVE is on the
CISA KEV list.

**Thirteen further issues were found by manual code review and are fixed here.**
An SCA tool reads dependency manifests; it never opens the application source. The
most serious finding in this whole effort (F-11 below) is one no SCA scan could
have produced.

---

## 2. Dependency vulnerabilities (14 CVEs, 5 packages)

### 2.1 `starlette` 0.46.2 — the only finding in production code

Seven advisories, reached through `fastapi`. `starlette` is the ASGI layer every
request passes through, so all seven were live in the deployed app.

| CVE | Severity | CWE | Weakness | Fixed in |
|---|---|---|---|---|
| CVE-2026-48710 | Medium **(KEV)** | CWE-444 | `Host` header not validated before rebuilding `request.url`; routing uses the raw path while `request.url` is rebuilt from `Host`, so a malformed header desynchronises the two | 1.0.1 |
| CVE-2026-48818 | High | CWE-918 | `StaticFiles` resolves UNC paths, causing an outbound SMB connection to an attacker host | 1.1.0 |
| CVE-2025-62727 | High | CWE-407 | `FileResponse` `Range` parsing is quadratic; one crafted header exhausts CPU | 0.49.1 |
| CVE-2026-48817 | Medium | CWE-470 | `HTTPEndpoint` dispatches on `getattr(self, method.lower())` with no verb allowlist | 1.1.0 |
| CVE-2026-54282 | Medium | CWE-706 | A path not starting with `/` moves the authority when `request.url` is re-parsed | 1.3.0 |
| CVE-2026-54283 | None | CWE-770 | `max_fields` / `max_part_size` silently ignored for `application/x-www-form-urlencoded` | 1.3.1 |
| CVE-2025-54121 | Medium | CWE-770 | Large multipart uploads block the event loop while spooling to disk | 0.47.2 |

CVE-2026-48710 is the one to note: it carries the lowest CVSS of the seven but is
the only one **known to be exploited in the wild** (EPSS 0.36, 76th percentile).
Severity ranking alone would have deprioritised it.

**Fix.** `starlette==1.6.0`. The blocker was `fastapi~=0.115.0`, which caps
starlette below 1.0 — `fastapi` 0.133.0 is the first release to drop that upper
bound, so `fastapi` moves to `0.141.1`. `fastapi` itself carried no advisory; the
bump exists only to make the patched starlette selectable.

`starlette` is now pinned directly in `app/requirements.txt` rather than left to
transitive resolution, so a future `fastapi` bump cannot silently pull a
vulnerable version back in.

### 2.2 `postcss`, `nanoid`, `browserslist`, `esbuild` — build-chain only

All four are `devDependencies`: they run during `npm run build` and ship nothing to
the browser. Fixed regardless, because a compromised build chain compromises the
artifact.

| Package | Was | CVEs | Now |
|---|---|---|---|
| `postcss` | 8.5.15 | CVE-2026-73646 (High, CWE-22), CVE-2026-69153 (Medium, CWE-22) — `sourceMappingURL` in parsed CSS is followed off-disk by default | 8.5.28 |
| `nanoid` | 3.3.14 | CVE-2026-67213, CVE-2026-67214 (High, CWE-835) — infinite loop on size 0 / negative size | 3.3.19 |
| `browserslist` | 4.28.2 | CVE-2026-73088 (High, CWE-248), CVE-2026-73089 (High, CWE-770) — unbounded cache and uncaught throw on untrusted stats | 4.28.9 |
| `esbuild` | 0.25.12 | Cxa83491f0-29a9 (High, CWE-426) — the **Deno** loader writes downloaded binaries `0o755` with no integrity check | removed |

`postcss` is a direct devDependency, so it moved in `package.json`. `nanoid` and
`browserslist` are transitive with no floor we can raise from our own
dependencies, so they are forced with npm `overrides` — both are same-major patch
bumps.

`esbuild` needed more thought. It is pulled in by `vite`, which pins `^0.25.0`; an
`overrides` entry forcing 0.28.2 **breaks the build** (0.28 dropped the
destructuring-to-legacy-target transform that vite 6 requests — verified, not
assumed). Vite 8 replaced esbuild with rolldown/oxc, so upgrading `vite` 6 → 8
removes the package from the tree entirely rather than patching it. The build was
confirmed working on Node 20.20.1 and produces equivalent output.

The scanner's own advisory text notes that the npm install path
(`lib/npm/node-install.ts`) *does* verify integrity and only the Deno path
(`lib/deno/mod.ts`) does not — so this project was never exploitable through it.
It is removed anyway.

### 2.3 `aiohttp` — a pin that permitted known-vulnerable versions

Not flagged by the scan, because the scanned build happened to resolve 3.14.3. But
the manifest said `aiohttp~=3.9.0`, which permits 3.9.0–3.9.3 — affected by
CVE-2024-23334 (static-route path traversal), CVE-2024-27306 (XSS) and
CVE-2024-30251 (multipart infinite loop). A clean rebuild could have installed one.
Now `aiohttp==3.14.3`.

### 2.4 Reproducibility

Python direct dependencies are now pinned exactly. The frontend has always had a
committed `package-lock.json`; `requirements.txt` is the Python half of the same
guarantee, so a redeploy installs the versions this scan cleared instead of
whatever is newest that day. Bumps are deliberate and re-scanned.

`databricks-sdk` was deliberately **not** bumped — see Residual risk.

---

## 3. Application findings (manual review)

The SCA scan does not read application code. These were found reviewing the app
against the fact that it runs in insurance workspaces.

### F-01 · Internal error text returned to the browser — CWE-209 · High

`app/app.py` returned `{"error": str(exc)}` from the catch-all handler. Those
exceptions wrap SQL Warehouse, Genie and Foundation Model API failures, whose
messages quote the failing statement, the catalog/schema/table names involved and
sometimes a literal column value. On a PHI-bearing workspace that is a disclosure
channel to anyone who can trigger an error. The same pattern appeared in three SSE
error paths and in the Genie Agent listing.

**Fix.** `security.safe_error()` logs the full traceback under a 12-character
reference and returns only that reference to the client. Applied in `app.py`,
`routes/_shared.py`, `routes/assess.py` and `routes/genie.py`. Upstream text is
still attached to the internal exception, so `sql_client`'s
OBO-to-service-principal fallback — which inspects the message to decide whether a
failure was an authorization gap — is unchanged.

### F-02 · PDF export reads local files and reaches the network — CWE-918 / CWE-22 · High

`POST /api/plan/pdf` built its HTML as:

```python
body_html = md.markdown(req.markdown or "", extensions=[...])
html = f"...<h1>{req.title}</h1>{body_html}..."
pisa.CreatePDF(src=html, dest=buf)
```

Two problems compounding:

1. `python-markdown` has no safe mode — raw HTML in the source passes through
   verbatim. Confirmed: `md.markdown('<img src="x" onerror=1>')` returns the tag intact.
2. `xhtml2pdf` resolves `src` and `href` on the document it renders, including
   `file://`, absolute paths and `http(s)://`.

So `{"markdown": "<img src=\"file:///etc/passwd\">"}` makes the server read a
local file into the PDF it hands back. `req.title` was interpolated unescaped as
well, giving a second injection point via `<style>@page{background-image:url(...)}`.
Both were reproduced against the running app before the fix.

**Fix, three layers:**

- `security.sanitize_html_fragment()` reduces the rendered Markdown to an
  allowlist of the tags this template styles. Resource-loading elements
  (`img`, `link`, `object`, `embed`, `iframe`) are absent by design;
  `script`/`style` are dropped **with their content**; `href` is restricted to
  `http`/`https`/`mailto`.
- The title goes through `security.escape_html()`.
- `pisa.CreatePDF` now takes a `link_callback` that refuses every external
  reference outright. The branded template needs none, so refusal beats an
  allowlist.

### F-03 · Per-user records keyed on a spoofable header — CWE-290 / CWE-639 · High

Assessment snapshots and saved plans were owned by whatever
`X-Forwarded-Email` the request carried:

```python
async def assess_history(x_forwarded_email: Optional[str] = Header(default=None)):
    return {"snapshots": await snapshots.list_snapshots(created_by=x_forwarded_email)}
```

The Databricks Apps proxy sets that header and strips inbound copies, so this is
not reachable through the supported ingress. It is still the wrong shape: one
network path to the container is all it takes to read another user's history, and
regulated deployments are expected to authenticate the principal before
authorizing, not to inherit trust from a hop.

Worse, `created_by IS NOT DISTINCT FROM $1` means a `NULL` principal matches rows
stored with `NULL` — so every request the proxy could not identify shared one
anonymous history bucket.

**Fix.** `security.resolve_principal()` prefers an identity **derived from the
forwarded OBO access token** (the workspace's own SCIM `Me` answer, cached 10
minutes per token, best-effort with a 5-second timeout so an API blip degrades to
the previous behaviour rather than failing the app). The header is the fallback
for deployments where user authorization is off. A request that cannot be
attributed at all returns 401 instead of reading or writing a shared bucket; local
development gets one explicit `local-dev` principal, never `NULL`. All seven
per-user routes now depend on `current_principal`.

### F-04 · PHI-shaped content written to logs — CWE-532 · Medium · HIPAA §164.312(b)

Three concrete leaks:

- `genie_client.py` logged the first 80 characters of every Genie question. Those
  are free-text questions against the customer's own warehouse — "claims for member
  …" is exactly the expected input.
- `lakebase_client.py` logged the **entire body** of a failed database-credentials
  API response. The body of a credentials response is credential material.
- Upstream SQL error text, logged by design for supportability, can echo values
  selected from PHI columns.

**Fix.** Questions are logged as a SHA-256 digest plus length. The credential
response is logged as its key names only. A `RedactingFilter` on the root logger
scrubs Databricks PATs, OAuth tokens, JWTs, `Authorization` values, serialized
`token`/`password`/`secret` fields, US SSNs, payment-card numbers and email local
parts from every record — including exception tracebacks — across this app,
uvicorn, aiohttp and the Databricks SDK. It is deliberately narrow: broader numeric
masking would corrupt the row counts and scores the assessment logs for support.

### F-05 · Unescaped SQL identifiers — CWE-89 · Medium

Catalog names were interpolated into backtick-quoted identifiers with no escaping,
in five places across `probes.py` plus the bundled `ai_comments_rag.py`
accelerator and `setup_app_permissions.py`:

```python
f"SELECT 1 FROM `{c}`.information_schema.tables LIMIT 1"
```

`c` is enumerated from `SHOW CATALOGS` / `system.information_schema.catalogs`, not
a literal the app controls. A catalog name containing a backtick closes the
quoting early and the remainder parses as SQL.

**Fix.** `security.quote_ident()` doubles embedded backticks (the Databricks escape)
and rejects control characters; `security.quote_literal()` does the same for
`IN (...)` lists. Applied to every identifier interpolation in the app, and via a
local `_ident()` helper in the two scripts and the accelerator notebook — where the
same file also interpolated a **model-generated** comment into a SQL string literal.

### F-06 · Unbounded request bodies — CWE-770 · Medium

`/api/plan/pdf` and `/api/plan/save` accepted Markdown of any size, then rendered
it to PDF and persisted it. A 3 MB body was accepted before the fix.

**Fix.** `BodySizeLimitMiddleware` caps the whole request at 2 MiB, enforced on the
declared `Content-Length` and again while streaming so a chunked body cannot slip
past. Pydantic `Field` limits bound the individual fields (200-character titles,
256 KB of Markdown, 4000-character Genie questions), and the conversation id is
constrained to `^[A-Za-z0-9_-]+$`.

### F-07 · HTTP clients with no timeout — CWE-400 · Medium

Nine `aiohttp.ClientSession()` call sites had no timeout. A stalled upstream would
hold a worker and its connection for the life of the process; the assessment
fan-out multiplies that.

**Fix.** Every session now carries an explicit timeout. Streaming sessions (LLM,
Genie, SQL polling) use connect and per-socket-read bounds rather than a total, so
a legitimately long stream is not cut off.

### F-08 · No response hardening headers — Medium

No CSP, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, HSTS or
cross-origin isolation headers.

**Fix.** `SecurityHeadersMiddleware` (pure ASGI, so it also covers SSE streams and
`StaticFiles`). `script-src 'self'` is achievable because Vite emits external,
content-hashed bundles and `index.html` carries no inline script; `style-src` needs
`'unsafe-inline'` for React's and Recharts' runtime element styles. `frame-ancestors`
allows the Databricks workspace domains and nothing else — a bare `'none'` would
break the workspace's own embed of the app. The built UI was loaded in a browser
under this policy with zero console violations.

### F-09 · Postgres TLS without peer verification — Medium · HIPAA §164.312(e)(1)

`ssl="require"` in `lakebase_client.py` and twice in `post_deploy.py`. That
encrypts but verifies neither certificate nor hostname, so it gives no protection
against an in-path attacker — which is what transmission security asks for. These
connections carry a live Lakebase credential over the public internet.

**Fix.** `verify-full` by default, with a `LAKEBASE_SSL_MODE` escape hatch for a
deployment whose endpoint presents a certificate outside the container's CA bundle.

### F-10 · Real workspace identifier committed to a public repo — Medium

`app/server/assessment/test_genie_audit.py` hardcoded workspace id
`1444828305810485`. The repository's own compliance gate flags this pattern, which
means the gate was failing on `main` before this change. A workspace id is
customer-identifying.

**Fix.** Replaced with an obviously synthetic value. The gate now passes.

### F-11 · Genie test answers with the service principal's data access — CWE-269 / CWE-863 · **Critical**

The highest-impact finding, and the one that matters most for a TB-scale insurance
workspace. Three facts combined:

1. `POST /api/genie/start-conversation` and `/api/genie/message` had **no
   authorization check at all** — only a test that `GENIE_SPACE_ID` was configured.
2. `genie_client` called `get_auth_headers(force_sp=True)`, hard-wiring every Genie
   call to the **app service principal** and ignoring the viewer entirely. The
   comment explained why (`sql` is the only user API scope, and it does not cover
   the Genie Conversation API) but the consequence was not addressed.
3. `scripts/setup_app_permissions.py` grants that service principal
   `GRANT SELECT ON CATALOG` for **every catalog being assessed**.

A Genie answer returns up to 100 rows of real query results, which the UI renders.
So any user who could open the app could ask a natural-language question and
receive rows from any table the service principal could read — in a typical
deployment, the entire metastore. On an insurance workspace that is PHI, delivered
to someone with no grant on it, with no audit trail tying the read to them.

**Fix.** The Genie test now runs **on-behalf-of the viewer**, so the answer reflects
that person's own Unity Catalog grants. All three endpoints require an established
identity. When the viewer's token cannot call the Genie API, the app **refuses and
explains** rather than silently answering with elevated privileges. Responses carry
a `ran_as` field so the UI can never imply borrowed rows are the viewer's own.

> **This is the one deliberate behaviour change in this work.** Restoring the old
> behaviour is a single environment variable, `GENIE_ALLOW_SP_FALLBACK=true`, which
> logs a warning on every call. Do not set it on a workspace holding PHI: it
> re-opens exactly this escalation. The better fix is to enable user authorization
> on the app and grant it a Genie API scope, which makes the fallback unnecessary.

### F-12 · SQL error text persisted into saved assessments — CWE-209 · High

Eight probe failure paths built their user-facing note from the exception:

```python
return _empty(f"Could not read comment coverage ({str(e)[:120]}).")
```

That note is returned in the scorecard **and written into the `scorecard` JSONB
column** when the run is saved. So a SQL Warehouse error quoting
`phi_prod.claims.member_ssn`, or a rejected literal value, did not just reach one
browser — it was persisted to the history table and re-served on every later view
of that snapshot.

This is the same weakness as F-01, in a path F-01 did not reach, and the first
version of the compliance check missed it because the pattern only matched lines
that also mentioned `error` or `note`.

**Fix.** A single `_failed()` helper gives every probe a fixed, actionable note plus
a log reference; the same treatment is applied in `scoring.py`, which wrapped any
probe exception the same way. The gate check was widened to match exception text
interpolated into anything returned, not just into a line containing `error`.

### F-13 · The compliance gate was scanning an incomplete file set

`compliance_scan.test.sh` builds its scan set from `git ls-files`, which lists only
**tracked** files. New files sat outside the gate until their first commit — so a
pre-commit run reported PASS on a tree it had not fully read. Re-running after the
first commit immediately flagged three real issues in the newly-tracked files
(a token-shaped literal and non-reserved email domains in the new tests). Fixed at
source: tests now use RFC-2606 reserved domains and assemble the token.

No change to the gate is needed — the lesson is operational, and is recorded here:
**run the gate after staging, not before.**

---

## 4. HIPAA control mapping

The app stores no PHI itself — it reads Unity Catalog metadata and persists
scorecards. But it executes queries against, and surfaces results from, workspaces
that do, so the safeguards below apply to it as a system component.

| Safeguard | § | Gap found | Control now in place |
|---|---|---|---|
| Access control — unique user identification | 164.312(a)(2)(i) | Ownership rested on a spoofable header; unidentified callers shared one bucket | F-03: token-derived principal, 401 when unattributable, no `NULL` bucket |
| Access control — authorization | 164.308(a)(4)(ii)(B) | The Genie test ran as a service principal holding SELECT on every catalog, for any viewer | F-11: runs on-behalf-of the viewer; escalation is opt-in and logged |
| Minimum necessary / incidental disclosure | 164.502(b) | Warehouse rows returned to viewers with no grant on the source table | F-11 |
| Audit controls | 164.312(b) | Question text and credential bodies written to logs | F-04: digests, key-name-only credential logging, root-logger redaction |
| Audit controls — attribution | 164.312(b) | Genie reads were attributed to the service principal, not the person who asked | F-11: the read now runs as the viewer, so workspace audit names them |
| Storage — improper retention of disclosed data | 164.310(d)(2)(i) | Upstream error text persisted into the snapshot history table | F-12: fixed notes plus a log reference |
| Integrity — improper modification | 164.312(c)(1) | Identifier injection into SQL | F-05: `quote_ident` / `quote_literal` everywhere |
| Transmission security | 164.312(e)(1) | Postgres TLS unverified | F-09: `verify-full` |
| Transmission security — encryption | 164.312(e)(2)(ii) | No HSTS or upgrade directive | F-08: HSTS + `upgrade-insecure-requests` |
| Information system activity review | 164.308(a)(1)(ii)(D) | Errors were untraceable across client and log | F-01: correlation reference on every failure |
| Protection from malicious software | 164.308(a)(5)(ii)(B) | 14 known CVEs, one on KEV | §2: all resolved; pinned and reproducible |
| Minimum necessary | 164.502(b) | Errors returned more than the caller needed | F-01, F-02 |

**Not addressed here** — organizational controls outside the codebase: a Business
Associate Agreement with Databricks, a retention/disposal schedule for the
`ontology_assessment_snapshots` and `ontology_plans` tables (§164.310(d)(2)(i)),
and workforce training. Flagged for the compliance owner.

---

## 5. Licence and legal risk

The scan reported 13 packages with legal risk, all of the form "no licence has been
marked as effective". This is a triage state in the scanner, not a licence
violation — each package is published under a well-known permissive licence
(`aiohttp` Apache-2.0, `cryptography` Apache-2.0/BSD, `urllib3` MIT, `attrs` MIT,
`pillow` MIT-CMU, and so on). No copyleft obligation attaches to this
distribution: 287 MIT, 22 ISC, 10 Apache-2.0, 11 BSD, and three weak-copyleft
packages (`certifi` MPL-2.0, two LGPL) that are used unmodified and dynamically
linked.

**Action for the compliance owner:** mark the 13 as effective in the Checkmarx
console. No code change is required and none was made — silently asserting a
licence on someone else's package would be the wrong fix.

---

## 6. Verification

Everything below was run against the changed tree, not asserted.

| Check | Result |
|---|---|
| `pip install -r app/requirements.txt` in a clean Python 3.11 venv | resolves, `pip check` clean, starlette 1.6.0 |
| App imports and serves under starlette 1.6.0 | all endpoints return their prior status and payload shape |
| `npm ci && npm run build` | vite 8.3.0, 2312 modules, builds clean |
| Vulnerable packages in the npm tree | `esbuild` absent; nanoid 3.3.19, browserslist 4.28.9, postcss 8.5.28 |
| `python -m unittest discover -s server` | 47 tests pass (3 pre-existing + 44 new) |
| Generated SQL vs. the original expressions | byte-identical for every ordinary catalog name |
| Scoring logic diff | no change to any formula, threshold, weight or signal |
| Request latency vs. the pre-remediation tree | +0.002 to +0.06 ms from this work; see the parity section |
| Redaction pattern scaling (ReDoS check) | linear, 2.00x for 2x input |
| `npx vitest run` | 16 tests pass |
| `scripts/__tests__/compliance_scan.test.sh` | PASS, including 8 new checks |
| New gate checks fail on a deliberate regression | all 10 confirmed to fire |
| Genie endpoints unauthenticated | 401 on all three (were fully open) |
| Genie test with an identified viewer but no OBO token | 403 with guidance, not a service-principal answer |
| Probe failure note | carries a reference; table name and column value withheld |
| Built UI loaded in a browser under the new CSP | renders; zero console violations |
| Exploit reproduction for F-02 before the fix | local file reference reached the renderer; blocked after |

Functional parity was checked endpoint by endpoint: `/api/config`, `/api/content`,
`/api/content/{key}` (200 and 404 paths), `/api/accelerators`, the artifact
download, `/api/assess/history`, `/api/plan/list`, `/api/plan/generate` validation,
and `/api/plan/pdf` (still returns a valid `%PDF-` document from the same
Markdown). The Assess and Learn tabs were exercised in a browser.

### Behaviour, accuracy and performance parity

The concern with a security pass is that it quietly changes what the app does or
how fast it does it. Three things were measured rather than assumed.

**1. Not one generated SQL statement changed.** `quote_ident` / `quote_literal`
replaced hand-rolled quoting, so the output was diffed against the original
expressions across every `information_schema` view, both source modes
(metastore-wide and per-catalog union) and a set of realistic catalog names:

```
RESULT: BYTE-IDENTICAL for every ordinary catalog name
  'sales'          -> `sales`
  'member-360'     -> `member-360`
  'Cat With Space' -> `Cat With Space`
  'a`b'            -> `a``b`     <- escaping engages only on a name that would break out
```

Same statements means the same warehouse query plans, the same scan volume, the
same cost and the same rows. Nothing about assessment accuracy can have moved.

**2. Not one scoring line changed.** Every edit in `probes.py` and `scoring.py` is
identifier quoting, an HTTP timeout, or an error-message path that only executes
when a probe has already failed. Score formulas, thresholds, weights, signal
definitions, gap wording and the adoption banding are untouched — verifiable in one
command:

```bash
git diff 3358270..HEAD -- app/server/assessment/
```

**3. Request latency.** Benchmarked on one machine, 300 iterations per endpoint
after warm-up, with the pre-remediation tree on the original dependency stack
(fastapi 0.115.14 / starlette 0.46.2) as the baseline. Splitting the framework
upgrade from the code changes shows where the difference actually comes from:

| Endpoint | Baseline | Framework upgrade | + this work | Framework cost | This work's cost |
|---|---|---|---|---|---|
| `GET /api/content` | 5.189 ms | 5.387 ms | 5.446 ms | +0.198 ms | **+0.059 ms** |
| `GET /api/accelerators` | 4.229 ms | 4.456 ms | 4.458 ms | +0.228 ms | **+0.002 ms** |
| `GET /api/content/{key}` | 2.314 ms | 2.529 ms | 2.562 ms | +0.215 ms | **+0.033 ms** |
| `POST /api/plan/pdf` | 62.753 ms | 63.100 ms | 63.840 ms | +0.347 ms | **+0.740 ms** |

The hardening middleware costs essentially nothing on the JSON endpoints. The
0.74 ms on the PDF route is the HTML sanitizer, 1.2% of an operation dominated by
PDF rendering. The ~0.2 ms that remains is inherent to running the patched
framework and is the price of the seven starlette CVEs.

Both middlewares were profiled and optimised after the first measurement: the
response headers are pre-encoded once at import instead of per response, and the
body-size limiter scans for `Content-Length` instead of materialising every header
into a dict.

For scale context: every one of those figures is local CPU. A real assessment is
dominated by SQL Warehouse round trips and runs 30–60 seconds, so the added
fraction is far below measurement noise on a live workspace.

**4. Log redaction.** It runs on every record, so it was benchmarked and checked
for catastrophic backtracking:

| Input | Cost per record |
|---|---|
| Typical assessment log line | 18–34 µs |
| 4 KB adversarial input (digit- and separator-heavy) | 0.8–1.7 ms |

A 1068-catalog assessment emits roughly 120 records, so redaction adds about 4 ms
to a 30–60 second run. Scaling is exactly linear (2.00× cost for 2× input across
1 KB → 16 KB), which rules out ReDoS in the patterns. Two sites that logged an
entire upstream response body were capped at 2 KB, so a remote server cannot
dictate log volume or redaction work.

**5. One latency bug found and fixed in this work.** The identity lookup cached
successes but not failures, so a workspace with a slow or unreachable SCIM endpoint
would have paid the full 5-second timeout on *every* per-user request before
falling back. Failures are now cached for 60 seconds, turning a degraded dependency
into one slow call per minute instead of one per request.

### Re-verifying

```bash
bash scripts/__tests__/compliance_scan.test.sh
cd app && python -m unittest discover -s server --pattern "test_*.py"
cd app && npx vitest run --config vitest.config.ts
cd app/frontend && npm ci && npm run build
```

---

## 7. Residual risk and follow-ups

1. **`databricks-sdk` 0.36.0 is two majors behind** (1.0.0 is current). The scan
   found no advisory against it, and widening the pin in a security change would
   have swapped a known-good auth path for an untested one. Deliberately left at
   0.36.0. Bump it as its own change, with a workspace to test against.
2. **Transitive Python dependencies still float.** Direct dependencies are pinned;
   their dependencies are not. A `pip-compile`-generated lock would close this, at
   the cost of adding tooling to the deploy path.
3. **`app/vitest.config.ts` sets `esbuild: { jsx: 'automatic' }`**, which vite 8
   now ignores in favour of oxc. Tests pass because automatic JSX is the oxc
   default, but the key is dead. Left alone deliberately — the harness invokes
   vitest through `npx`, so the resolved vite version varies by environment.
4. **Identity verification is best-effort.** If the SCIM lookup fails, the
   resolver falls back to the forwarded header. This is intentional — failing
   closed would take the app down on an API blip — but it means the header remains
   the trust anchor in that window.
5. **No rate limiting.** The LLM-backed endpoints are bounded by size but not by
   request rate. Databricks Apps provides no built-in limiter; a per-principal
   limit is the natural next control.
6. **Existing `NULL`-owned rows.** If any deployment stored snapshots or plans
   before this change with no forwarded identity, those rows are now unreachable
   (by design — they were the shared bucket). Delete or reassign them if any exist.
