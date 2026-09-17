"""Unit tests for cloud-aware documentation link rewriting (server.doc_links).

Pure string logic, no Databricks/network imports, so these run in the dependency-
free CI gate alongside test_pillars.
"""

from server.doc_links import (
    AWS_ONLY_PATHS,
    cloud_from_host,
    cloud_doc_url,
    rewrite_doc_links_in_text,
)

AWS = "https://docs.databricks.com/aws/en/genie-agents/best-practices"


def test_cloud_from_host_detects_each_cloud():
    assert cloud_from_host("https://adb-123.4.azuredatabricks.net") == "azure"
    assert cloud_from_host("https://123.4.gcp.databricks.com") == "gcp"
    assert cloud_from_host("https://dbc-abc.cloud.databricks.com") == "aws"
    # Unknown / empty defaults to aws (the authored-against cloud).
    assert cloud_from_host("") == "aws"
    assert cloud_from_host("https://example.com") == "aws"


def test_cloud_doc_url_aws_is_unchanged():
    assert cloud_doc_url(AWS, "aws") == AWS


def test_cloud_doc_url_gcp_swaps_base_keeps_path():
    assert cloud_doc_url(AWS, "gcp") == "https://docs.databricks.com/gcp/en/genie-agents/best-practices"


def test_cloud_doc_url_azure_routes_to_microsoft_learn():
    assert cloud_doc_url(AWS, "azure") == (
        "https://learn.microsoft.com/en-us/azure/databricks/genie-agents/best-practices"
    )


def test_cloud_doc_url_passes_non_databricks_links_through():
    for url in (
        "https://github.com/databricks-solutions/databricks-genie-workbench",
        "https://www.databricks.com/blog/some-post",
        "https://learn.microsoft.com/en-us/azure/databricks/genie",
        "",
    ):
        for cloud in ("aws", "azure", "gcp"):
            assert cloud_doc_url(url, cloud) == url


def test_cloud_doc_url_aws_only_paths_fall_back_to_aws():
    # Simulate a page with no non-AWS equivalent by checking the fallback contract
    # against whatever is currently listed (empty today, so this is vacuously safe;
    # the assertion documents the intended behavior).
    for path in AWS_ONLY_PATHS:
        url = "https://docs.databricks.com/aws/en/" + path
        assert cloud_doc_url(url, "azure") == url
        assert cloud_doc_url(url, "gcp") == url


def test_rewrite_doc_links_in_text():
    text = (
        "- Curate: https://docs.databricks.com/aws/en/genie-agents/best-practices\n"
        "- Repo: https://github.com/databricks-solutions/x\n"
    )
    out = rewrite_doc_links_in_text(text, "gcp")
    assert "docs.databricks.com/gcp/en/genie-agents/best-practices" in out
    assert "github.com/databricks-solutions/x" in out  # untouched
    # aws is a no-op.
    assert rewrite_doc_links_in_text(text, "aws") == text
