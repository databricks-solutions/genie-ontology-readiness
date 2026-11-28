"""Manage stateless, ephemeral PR preview apps without Asset Bundles.

Example: python scripts/pr_preview.py up --pr-number 123 --profile <profile>
         --warehouse-id <warehouse-id>
Profile defaults to DATABRICKS_PROFILE, then DATABRICKS_CONFIG_PROFILE.
Warehouse defaults to WAREHOUSE_ID, then DATABRICKS_WAREHOUSE_ID.
Dry-run prints the create-if-missing path; it runs no subprocesses (including npm).
Source staging is isolated per preview under /Workspace/Shared; deleting the app
also removes that preview's staging directory.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def pr_number(value):
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise argparse.ArgumentTypeError("PR number must be a positive integer")
    return int(value)


def app_name(number):
    return f"genie-ontology-readiness-pr-{number}"


class CLI:
    def __init__(self, profile, dry_run):
        self.profile = profile
        self.dry_run = dry_run

    def run(self, *args, missing_ok=False):
        # Match post_deploy.py: resolve the CLI through PATH.
        command = ["databricks", *args, "--profile", self.profile]
        print(f"$ {shlex.join(command)}")
        if self.dry_run:
            return None
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            message = result.stdout + result.stderr
            lower = message.lower()
            absent = any(marker in lower for marker in ("resource_does_not_exist", "path_not_found"))
            # Some CLI versions print only the API message, without its error code.
            target = args[2] if len(args) > 2 else ""
            absent = absent or (bool(target) and target.lower() in lower
                                and ("does not exist" in lower or "not found" in lower))
            if missing_ok and absent:
                return None
            raise RuntimeError(f"{shlex.join(command)} failed: {message.strip()}")
        return result.stdout

    def get_app(self, name):
        raw = self.run("apps", "get", name, "--output", "json", missing_ok=True)
        if raw is None:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("name") != name:
            raise RuntimeError("apps get returned an unexpected app response")
        return data


def matching_resources(resources, warehouse_id):
    if len(resources) != 1:
        return False
    resource = resources[0]
    warehouse = resource.get("sql_warehouse") or {}
    return (resource.get("name") == "sql-warehouse" and warehouse.get("id") == warehouse_id
            and warehouse.get("permission") == "CAN_USE")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "down"))
    parser.add_argument("--pr-number", required=True, type=pr_number)
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_PROFILE")
                        or os.environ.get("DATABRICKS_CONFIG_PROFILE"))
    parser.add_argument("--warehouse-id", default=os.environ.get("WAREHOUSE_ID")
                        or os.environ.get("DATABRICKS_WAREHOUSE_ID"))
    parser.add_argument("--source-path", type=Path, default=APP_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.profile or not args.profile.strip():
        parser.error("--profile or DATABRICKS_PROFILE / DATABRICKS_CONFIG_PROFILE is required")
    if args.command == "up" and (not args.warehouse_id or not args.warehouse_id.strip()):
        parser.error("up requires --warehouse-id or WAREHOUSE_ID / DATABRICKS_WAREHOUSE_ID")
    name = app_name(args.pr_number)
    remote = f"/Workspace/Shared/.gor-pr-previews/{name}"
    cli = CLI(args.profile, args.dry_run)
    try:
        if args.command == "down":
            cli.run("apps", "delete", name, missing_ok=True)
            cli.run("workspace", "delete", remote, "--recursive", missing_ok=True)
            return 0
        source = args.source_path.resolve()
        if not (source / "package.json").is_file() or not (source / "frontend").is_dir():
            raise RuntimeError("--source-path must point to the app directory containing package.json and frontend/")
        print(f"$ (cd {shlex.quote(str(source))} && npm run build)")
        if not args.dry_run:
            subprocess.run(["npm", "run", "build"], cwd=source, check=True)
            if not (source / "frontend" / "dist" / "index.html").is_file():
                raise RuntimeError("Frontend build did not produce frontend/dist/index.html")
        existing = cli.get_app(name)
        resources = [{"name": "sql-warehouse", "sql_warehouse": {
            "id": args.warehouse_id, "permission": "CAN_USE",
        }}]
        if existing is None:
            if args.dry_run:
                print("# If the app is absent, create it; otherwise skip create.")
            cli.run("apps", "create", name, "--json", json.dumps({"name": name, "resources": resources}))
        elif not matching_resources(existing.get("resources", []), args.warehouse_id):
            raise RuntimeError("Existing preview resources differ; run down before recreating with this warehouse")
        with tempfile.TemporaryDirectory(prefix="gor-pr-preview-") as tmp:
            local = Path(tmp) / "app"
            if not args.dry_run:
                shutil.copytree(source, local, ignore=shutil.ignore_patterns(
                    "node_modules", "__pycache__", ".pytest_cache", "*.pyc", ".env", ".env.*", ".git",
                ))
            cli.run("sync", "--full", str(local), remote)
            cli.run("apps", "deploy", name, "--source-code-path", remote,
                    "--mode", "SNAPSHOT", "--auto-approve")
        deployed = cli.get_app(name)
        if not args.dry_run:
            if not deployed or not deployed.get("url"):
                raise RuntimeError("Deployment completed but apps get returned no app URL")
            print(deployed["url"])
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
