"""Workspace-agnostic contributor previews, invoked through scripts/preview.sh."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def validate_slug(value):
    """Return the canonical slug shared by summary, deploy, run, and destroy."""
    slug = value.strip()
    if not slug:
        raise ValueError("preview requires a non-empty PREVIEW_SLUG / preview_slug (PR number or branch slug).")
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", slug):
        raise ValueError("PREVIEW_SLUG must contain only lowercase letters, digits, and hyphens, "
                         "and start/end with a letter or digit.")
    if len(f"gor-pr-{slug}") > 30:
        raise ValueError("PREVIEW_SLUG must be at most 23 characters so the app name is at most 30 characters.")
    return slug


def clear_local_preview_state(root=None):
    """Discard target-keyed cache so the CLI loads the selected slug's remote state.

    A new slug may have no remote state yet; retaining another slug's local state
    would let Terraform reconcile that other app. Never remove other targets.
    """
    cache = (ROOT if root is None else root) / ".databricks" / "bundle" / "preview"
    try:
        shutil.rmtree(cache)
    except FileNotFoundError:
        pass


def derive_slug(*, pr=None, slug=None, branch=None):
    if pr is not None:
        if not re.fullmatch(r"[0-9]+", pr) or int(pr) < 1:
            raise ValueError("--pr must be a positive PR number.")
        return validate_slug(str(int(pr)))
    if slug is not None:
        return validate_slug(slug)
    if not branch or branch == "HEAD":
        raise ValueError("Cannot derive a slug from detached HEAD; pass --pr or --slug.")
    return validate_slug(re.sub(r"[^a-z0-9-]+", "-", branch.lower()).strip("-"))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scripts/preview.sh",
        description="Deploy or destroy a stateless gor-pr-<slug> app in your chosen workspace.",
        epilog="Auth: --profile, DATABRICKS_PROFILE, or normal CLI defaults (including "
               "DATABRICKS_HOST + token). Deploy requires WAREHOUSE_ID in that workspace, "
               "Node/npm and Python dependencies (requirements-dev.txt). Optional: "
               "ASSESS_CATALOGS and GENIE_SPACE_ID, default empty. No automatic catalog/system grants; "
               "unavailable data degrades gracefully. Destroy needs no warehouse and asks for confirmation "
               "unless --yes / --auto-approve is passed (required for unattended teardown). "
               "Use the same workspace, slug and deploying identity for teardown (per-user bundle state).",
    )
    parser.add_argument("action", choices=("deploy", "destroy"))
    parser.add_argument("--profile", help="Your Databricks CLI profile; otherwise use normal CLI auth resolution")
    parser.add_argument("--pr", help="PR number (takes precedence over --slug)")
    parser.add_argument("--slug", help="Explicit lowercase/digit/hyphen identifier, starting/ending with a letter or digit, "
                        "at most 23 characters; "
                        "otherwise sanitize the current branch")
    parser.add_argument("--yes", "--auto-approve", action="store_true",
                        help="Skip destroy confirmation (required for unattended/agent teardown)")
    args = parser.parse_args(argv)
    if args.yes and args.action != "destroy":
        parser.error("--yes / --auto-approve applies only to destroy.")
    env = os.environ.copy()
    if args.profile is not None:
        if not args.profile.strip():
            parser.error("--profile must not be empty; omit it to use default CLI auth.")
        env["DATABRICKS_PROFILE"] = args.profile
    profile = env.get("DATABRICKS_PROFILE", "")
    auth_args = ["--profile", profile] if profile else []
    try:
        branch = None
        if args.pr is None and args.slug is None:
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT,
                                    check=True, capture_output=True, text=True).stdout.strip()
        slug = derive_slug(pr=args.pr, slug=args.slug, branch=branch)
        warehouse = env.get("WAREHOUSE_ID", "").strip()
        if args.action == "deploy" and not warehouse:
            parser.error("Set WAREHOUSE_ID to a SQL warehouse in the target workspace.")
        auth = subprocess.run(["databricks", "current-user", "me", *auth_args, "--output", "json"],
                              cwd=ROOT, env=env, capture_output=True, text=True)
        if auth.returncode:
            parser.error("Cannot authenticate to the target workspace. Pass --profile <name>, set "
                         "DATABRICKS_PROFILE / DATABRICKS_HOST with credentials, or configure CLI default auth. "
                         + auth.stderr.strip())
        print(f"{args.action.capitalize()} preview gor-pr-{slug} using {profile or 'CLI default auth'}", flush=True)
        if args.action == "destroy":
            clear_local_preview_state()
            approval_args = ["--auto-approve"] if args.yes else []
            subprocess.run(["databricks", "bundle", "destroy", "-t", "preview",
                            "--var", f"preview_slug={slug}", *auth_args, *approval_args], cwd=ROOT, env=env, check=True)
            return
        # Build from this checkout so a contributor needs no pre-existing artifacts.
        frontend = ROOT / "app" / "frontend"
        subprocess.run(["npm", "ci"], cwd=frontend, check=True, env=env)
        subprocess.run(["npm", "run", "build"], cwd=frontend, check=True, env=env)
        env.update(TARGET="preview", PREVIEW_SLUG=slug, WAREHOUSE_ID=warehouse,
                   INFRA="true", RESET_SETTINGS="true", USE_LAKEBASE="false",
                   LAKEBASE_INSTANCE_NAME="", LAKEBASE_ENDPOINT_NAME="", LAKEBASE_DATABASE="",
                   ASSESS_CATALOGS=env.get("ASSESS_CATALOGS", ""), GENIE_SPACE_ID=env.get("GENIE_SPACE_ID", ""),
                   BUNDLE_VAR_lakebase_instance_name="")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "post_deploy.py")], cwd=ROOT, env=env, check=True)
        app = subprocess.run(["databricks", "apps", "get", f"gor-pr-{slug}", *auth_args, "--output", "json"],
                             cwd=ROOT, env=env, check=True, capture_output=True, text=True)
        url = json.loads(app.stdout).get("url")
        if not url:
            raise ValueError("The Apps API returned no preview URL; inspect the app with databricks apps get.")
        print(url)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")


if __name__ == "__main__":
    main()
