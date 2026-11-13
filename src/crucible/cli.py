"""Deploy Bicep and manage sandbox workspaces through the Azure SDK."""

import argparse
import logging
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from azure.core.exceptions import AzureError

from . import __version__
from .deployment import deploy
from .models import Arguments
from .workspaces import run_workspace

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Define commands with explicit Azure scope and opt-in mutation.

    Returns:
        The configured top-level argument parser.
    """
    parser = argparse.ArgumentParser(prog="crucible", description=__doc__)
    _ = parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    deploy_parser = commands.add_parser(
        "deploy", help="Deploy infrastructure and missing workspaces"
    )
    deploy_parser.set_defaults(action="deploy")
    _ = deploy_parser.add_argument("--subscription", required=True, help="Customer subscription ID")
    _ = deploy_parser.add_argument(
        "--resource-group", required=True, help="Target resource group; created if missing"
    )
    _ = deploy_parser.add_argument(
        "--parameters", type=Path, required=True, help="Bicep parameter file"
    )
    _ = deploy_parser.add_argument("--deployment", default="crucible", help="ARM deployment name")
    mode = deploy_parser.add_mutually_exclusive_group()
    _ = mode.add_argument(
        "--apply", action="store_true", help="Deploy infrastructure and workspaces"
    )
    _ = mode.add_argument("--what-if", action="store_true", help="Preview ARM changes only")
    workspace = commands.add_parser(
        "workspace", help="Operate sessions using deployed Bicep settings"
    )
    actions = workspace.add_subparsers(dest="action", required=True)
    for action, description in (
        ("provision", "Create missing declared workspaces and storage volumes"),
        ("create", "Create a sandbox with an existing volume"),
        ("bootstrap", "Set up missing tools on one running workspace"),
        ("auto-suspend", "Enable or disable idle suspension on one existing workspace"),
        ("stop", "Suspend one workspace"),
        ("resume", "Resume one workspace"),
    ):
        command = actions.add_parser(action, help=description)
        _ = command.add_argument("--subscription", required=True, help="Customer subscription ID")
        _ = command.add_argument(
            "--resource-group", required=True, help="Deployment resource group"
        )
        _ = command.add_argument("--deployment", default="crucible", help="Deployment to read")
        if action != "provision":
            _ = command.add_argument("--name", required=True, help="Unique workspace label")
        _ = command.add_argument(
            "--apply", action="store_true", help="Apply rather than plan changes"
        )
        if action == "create":
            _ = command.add_argument("--volume", required=True, help="Existing storage volume name")
            _ = command.add_argument(
                "--read-only", action="store_true", help="Mount an existing Blob volume read-only"
            )
        if action == "auto-suspend":
            suspension = command.add_mutually_exclusive_group(required=True)
            _ = suspension.add_argument(
                "--enable",
                dest="auto_suspend_enabled",
                action="store_true",
                help="Enable idle suspension using the existing timeout",
            )
            _ = suspension.add_argument(
                "--disable",
                dest="auto_suspend_enabled",
                action="store_false",
                help="Disable idle suspension; running compute remains billable",
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Plan or execute operations and report failures.

    Args:
        argv: Arguments excluding the executable name, or None for process arguments.

    Returns:
        0 for success, 1 for runtime/API failure, or 2 for invalid settings.

    Raises:
        SystemExit: Argument parsing handles help, version, or invalid syntax.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    logging.getLogger("azure").setLevel(logging.ERROR)
    args = Arguments()
    _ = build_parser().parse_args(argv, namespace=args)
    try:
        if not args.apply and not (args.action == "deploy" and args.what_if):
            LOGGER.info("Plan only; Azure reads may occur, but no resources are changed.")
        if args.action == "deploy":
            deploy(args)
            if args.apply:
                args.action = "provision"
                run_workspace(args)
        else:
            run_workspace(args)
    except subprocess.CalledProcessError as error:
        stderr = cast("object", error.stderr)
        error_message = f"Azure CLI exited with status {error.returncode}: {stderr or ''}"
        status = 1
    except (AzureError, OSError, RuntimeError) as error:
        error_message = str(error)
        status = 1
    except ValueError as error:
        error_message = str(error)
        status = 2
    else:
        return 0
    LOGGER.error("Error: %s", error_message)
    LOGGER.error("No automatic rollback was attempted. Inspect resources before retrying.")
    return status


if __name__ == "__main__":
    sys.exit(main())
