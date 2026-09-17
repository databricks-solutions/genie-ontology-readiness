"""Cloud-aware Databricks documentation links.

Every embedded docs link is authored against the AWS docs
(``https://docs.databricks.com/aws/en/<path>``). On an Azure or GCP deployment
those point at the wrong cloud's docs, so we rewrite the base at serve time to
match the cloud the app is running on:

    AWS    https://docs.databricks.com/aws/en/<path>
    GCP    https://docs.databricks.com/gcp/en/<path>
    Azure  https://learn.microsoft.com/en-us/azure/databricks/<path>

The page ``<path>`` is identical across all three clouds (verified for every doc
page this app links to), so no per-link mapping table is needed. The rare page
that has no equivalent on a non-AWS cloud is listed in ``AWS_ONLY_PATHS`` and
falls back to the AWS URL. ``scripts/verify_doc_links.py`` HEAD-checks coverage
across clouds so that set stays accurate; nothing here makes network calls.

This module is intentionally dependency-free (pure string logic) so it can be
unit-tested without the app's runtime dependencies.
"""

import re

_AWS_PREFIX = "https://docs.databricks.com/aws/en/"
_GCP_PREFIX = "https://docs.databricks.com/gcp/en/"
_AZURE_PREFIX = "https://learn.microsoft.com/en-us/azure/databricks/"

# Doc paths (the part after ".../aws/en/") that exist on AWS but have no
# equivalent on Azure and/or GCP — these always resolve to the AWS URL. Empty
# today: every page this app links to is present on all three clouds. Keep it in
# sync with scripts/verify_doc_links.py.
AWS_ONLY_PATHS: frozenset[str] = frozenset()


def cloud_from_host(host: str) -> str:
    """Infer the cloud provider (``aws`` | ``azure`` | ``gcp``) from a workspace host.

    Defaults to ``aws`` for an empty/unknown host (the authored-against cloud).
    """
    h = (host or "").lower()
    if "azuredatabricks.net" in h:
        return "azure"
    if "gcp.databricks.com" in h:
        return "gcp"
    return "aws"


def cloud_doc_url(url: str, cloud: str) -> str:
    """Rewrite an AWS Databricks docs URL to the given cloud, else return it as-is.

    Only ``https://docs.databricks.com/aws/en/...`` URLs are rewritten; any other
    link (GitHub, blog, Microsoft Learn, etc.) passes through untouched. Paths in
    ``AWS_ONLY_PATHS`` and the ``aws`` cloud always return the original AWS URL.
    """
    if not url or not url.startswith(_AWS_PREFIX):
        return url
    path = url[len(_AWS_PREFIX):]
    if cloud == "aws" or path in AWS_ONLY_PATHS:
        return url
    if cloud == "gcp":
        return _GCP_PREFIX + path
    if cloud == "azure":
        return _AZURE_PREFIX + path
    return url


# Matches a bare AWS docs URL inside free text (stops at whitespace or common
# Markdown delimiters), for rewriting links embedded in served artifacts.
_AWS_URL_RE = re.compile(r"https://docs\.databricks\.com/aws/en/[^\s)\]\"'>]+")


def rewrite_doc_links_in_text(text: str, cloud: str) -> str:
    """Route every AWS Databricks docs URL embedded in ``text`` to ``cloud``."""
    if cloud == "aws":
        return text
    return _AWS_URL_RE.sub(lambda m: cloud_doc_url(m.group(0), cloud), text)
