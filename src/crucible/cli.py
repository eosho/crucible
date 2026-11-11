"""Deploy Bicep and manage sandbox workspaces through the Azure SDK."""

import argparse
import json
import logging
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from azure.containerapps.sandbox import (
    ApiVersion,
    AutoDeletePolicy,
    AutoSuspendPolicy,
    LifecyclePolicy,
    Sandbox,
    SandboxClient,
    SandboxGroupClient,
    SandboxVolume,
    Volume,
    endpoint_for_region,
)
from azure.core.exceptions import AzureError
from azure.core.polling import LROPoller
from azure.identity import AzureCliCredential

from . import __version__
from .bootstrap import bootstrap_tools
from .models import WORKSPACE_NAME, WorkspaceDefinition, WorkspaceSettings, is_record

LOGGER = logging.getLogger(__name__)


class WorkspaceError(RuntimeError):
    """A workspace conflict or incompatible deployment output."""


class Arguments(argparse.Namespace):
    """Typed command arguments populated by argparse."""

    action: str
    subscription: str
    resource_group: str
    deployment: str
    name: str
    parameters: Path
    volume: str
    apply: bool
    what_if: bool
    auto_suspend_enabled: bool


def run_az(
    arguments: Sequence[str], *, capture_output: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run Azure CLI for Bicep compilation, deployment, and infrastructure reads.

    Args:
        arguments: Azure CLI arguments without the executable name.
        capture_output: Whether to capture output as text.

    Returns:
        The completed process, including any captured output.

    Raises:
        FileNotFoundError: Azure CLI is not installed.
        subprocess.CalledProcessError: The command fails.
        OSError: The process cannot be started.
    """
    executable = shutil.which("az")
    if executable is None:
        message = "Azure CLI is not installed on PATH"
        raise FileNotFoundError(message)
    # Resolve the fixed executable and pass arguments without a shell.
    return subprocess.run(  # noqa: S603
        [str(Path(executable).resolve()), *arguments],
        shell=False,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def deploy(args: Arguments) -> None:
    """Deploy infrastructure and then provision missing declared workspaces.

    Args:
        args: Subscription, target group, deployment name, and Bicep parameters.

    Raises:
        ValueError: The Bicep parameter file or its explicit location is invalid.
        subprocess.CalledProcessError: Azure CLI rejects the deployment.
        WorkspaceError: Group existence or compiled parameters are incompatible.
        AzureError: Workspace provisioning fails after infrastructure deployment.
    """
    parameters = args.parameters.resolve()
    if parameters.suffix != ".bicepparam" or not parameters.is_file():
        message = f"Specify an existing .bicepparam file: {parameters}"
        raise ValueError(message)
    compiled = read_json_object(
        run_az(
            ["bicep", "build-params", "--file", str(parameters), "--stdout"], capture_output=True
        ).stdout
    )
    template = compiled.get("templateJson")
    values = compiled.get("parametersJson")
    if not isinstance(template, str) or not isinstance(values, str):
        message = "Use a parameter file referencing the local subscription-scoped Bicep entry point"
        raise WorkspaceError(message)
    document = read_json_object(values)
    inputs = document.get("parameters")
    location = inputs.get("location") if is_record(inputs) else None
    region = location.get("value") if is_record(location) else None
    if not is_record(inputs) or not isinstance(region, str) or not region:
        message = "Set an explicit location in the Bicep parameter file"
        raise ValueError(message)
    exists = cast(
        "object",
        json.loads(
            run_az(
                [
                    "group",
                    "exists",
                    "--subscription",
                    args.subscription,
                    "--name",
                    args.resource_group,
                ],
                capture_output=True,
            ).stdout
        ),
    )
    if type(exists) is not bool:
        message = "Azure CLI returned an invalid resource-group existence result"
        raise WorkspaceError(message)
    inputs["resourceGroupName"] = {"value": args.resource_group}
    inputs["createResourceGroup"] = {"value": not exists}
    LOGGER.info(
        "%s resource group %s; deploy %s; %s",
        "Reuse" if exists else "Create",
        args.resource_group,
        args.deployment,
        "preview ARM only." if args.what_if else "then provision missing declared workspaces.",
    )
    if not args.apply and not args.what_if:
        return
    with TemporaryDirectory(prefix="crucible-deploy-") as directory:
        template_path = Path(directory) / "template.json"
        parameters_path = Path(directory) / "parameters.json"
        _ = template_path.write_text(template)
        _ = parameters_path.write_text(json.dumps(document))
        command = [
            "deployment",
            "sub",
            "what-if" if args.what_if else "create",
            "--subscription",
            args.subscription,
            "--location",
            region,
            "--name",
            args.deployment,
            "--template-file",
            str(template_path),
            "--parameters",
            f"@{parameters_path}",
        ]
        LOGGER.info("az %s", shlex.join(command))
        _ = run_az(command)
    if args.apply:
        args.action = "provision"
        run_workspace(args)


def read_json_object(text: str) -> dict[str, object]:
    """Decode an Azure CLI JSON object.

    Args:
        text: Captured JSON output.

    Returns:
        An object for the caller's contract checks.

    Raises:
        ValueError: The response is invalid JSON or not an object.
    """
    value = cast("object", json.loads(text))
    if not is_record(value):
        message = "Azure CLI returned an invalid JSON object"
        raise ValueError(message)
    return value


def load_workspace_settings(args: Arguments) -> WorkspaceSettings:
    """Read the selected deployment's non-secret settings.

    Args:
        args: Subscription, resource group, and deployment to read.

    Returns:
        Typed workspace defaults and inventory.

    Raises:
        WorkspaceError: Deployment outputs are missing or incompatible.
        subprocess.CalledProcessError: The deployment read fails.
    """
    result = run_az(
        [
            "deployment",
            "sub",
            "show",
            "--subscription",
            args.subscription,
            "--name",
            args.deployment,
            "--query",
            "properties.outputs.workspaceSettings.value",
            "--output",
            "json",
        ],
        capture_output=True,
    )
    try:
        value = cast("object", json.loads(result.stdout))
        if not is_record(value) or value.get("resourceGroup") != args.resource_group:
            message = (
                "Deployment outputs do not match --resource-group; deploy the current Bicep first"
            )
            raise WorkspaceError(message)
        return WorkspaceSettings.from_outputs(
            value, subscription_id=args.subscription, resource_group=args.resource_group
        )
    except (TypeError, ValueError) as error:
        message = f"Cannot read workspace settings: {error}"
        raise WorkspaceError(message) from error


def create_workspace(
    client: SandboxGroupClient, settings: WorkspaceSettings, name: str, volume: str
) -> None:
    """Create one sandbox with its disk attached and deletion disabled.

    Args:
        client: Group-scoped SDK client.
        settings: Deployed image, sizing, network, and lifecycle defaults.
        name: Unique workspace label.
        volume: Existing dedicated Data Disk name.

    Raises:
        AzureError: Sandbox creation or policy configuration fails.
        RuntimeError: The SDK reports a terminal provisioning failure.
        TimeoutError: The sandbox does not become running in time.
    """
    # The preview SDK omits the poller's documented result type.
    creation = cast(
        "LROPoller[SandboxClient]",
        client.begin_create_sandbox(
            disk=settings.image,
            cpu=settings.cpu,
            memory=settings.memory,
            disk_size=settings.root_disk_size,
            customer_vnet_connection_name=settings.vnet_connection,
            auto_suspend_seconds=settings.idle_timeout_seconds,
            auto_suspend_mode="Disk",
            labels={"name": name, "managed-by": "crucible", "workspace": name, "volume": volume},
            volumes=[SandboxVolume(volume_name=volume, mountpoint="/workspaces")],
        ),
    )
    sandbox = creation.result()
    _ = sandbox.set_lifecycle_policy(
        LifecyclePolicy(
            auto_suspend=AutoSuspendPolicy(
                enabled=settings.auto_suspend_enabled,
                interval=settings.idle_timeout_seconds,
                mode="Disk",
            ),
            auto_delete=AutoDeletePolicy(enabled=False),
        )
    )
    if settings.bootstrap_tools:
        LOGGER.info("Bootstrap missing tools for workspace %s.", name)
        bootstrap_tools(sandbox)


def configure_auto_suspend(sandbox: SandboxClient, *, enabled: bool, apply: bool) -> None:
    """Toggle idle suspension without changing its timeout, mode, or deletion policy.

    Args:
        sandbox: Existing workspace client; the workspace is not resumed.
        enabled: Whether idle suspension should be enabled.
        apply: Whether to update the policy rather than only log the plan.

    Raises:
        WorkspaceError: The existing policy is incomplete or the update is unconfirmed.
        AzureError: Reading or updating the lifecycle policy fails.
    """
    LOGGER.info(
        "%s idle auto-suspend; %s",
        "Enable" if enabled else "Disable",
        "preserve the current timeout." if enabled else "running compute remains billable.",
    )
    if not apply:
        return
    policy = sandbox.get().lifecycle
    if policy is None or policy.auto_suspend is None or policy.auto_delete is None:
        message = (
            "Workspace lifecycle policy is incomplete; inspect it before changing auto-suspend"
        )
        raise WorkspaceError(message)
    if (
        policy.auto_suspend.mode != "Disk"
        or policy.auto_suspend.interval is None
        or policy.auto_suspend.interval < 1
    ):
        message = "Workspace requires a Disk suspension policy with a positive idle interval"
        raise WorkspaceError(message)
    desired = replace(policy, auto_suspend=replace(policy.auto_suspend, enabled=enabled))
    updated = sandbox.set_lifecycle_policy(desired)
    if updated.auto_suspend is None or updated.auto_suspend.enabled != enabled:
        message = "Auto-suspend update was not confirmed; inspect the workspace before retrying"
        raise WorkspaceError(message)
    LOGGER.info("Idle auto-suspend is %s.", "enabled" if enabled else "disabled")


def validate_existing_workspace(
    workspace: WorkspaceDefinition, sandbox: Sandbox, volume: Volume | None
) -> None:
    """Require a complete owned resource pair without modifying existing work.

    Args:
        workspace: Declared workspace being inspected.
        sandbox: Existing sandbox with the matching name label.
        volume: Its dedicated volume, if present.

    Raises:
        WorkspaceError: Ownership, attachment, or lifecycle state needs inspection.
    """
    labels = {"managed-by": "crucible", "workspace": workspace.name}
    if (
        volume is None
        or any(sandbox.labels.get(key) != value for key, value in labels.items())
        or any(volume.labels.get(key) != value for key, value in labels.items())
        or sandbox.labels.get("volume") != workspace.volume_name
    ):
        message = f"{workspace.name}: incomplete workspace or ownership conflict; inspect it first"
        raise WorkspaceError(message)
    policy = sandbox.lifecycle
    if (
        volume.type != "DataDisk"
        or volume.is_attached is not True
        or sandbox.state not in ("Running", "Stopped")
        or policy is None
        or policy.auto_suspend is None
        or policy.auto_suspend.mode != "Disk"
        or policy.auto_delete is None
        or policy.auto_delete.enabled
    ):
        message = f"{workspace.name}: partial or transitional workspace; recover it explicitly"
        raise WorkspaceError(message)


def provision_workspaces(
    client: SandboxGroupClient, settings: WorkspaceSettings, *, apply: bool
) -> None:
    """Preflight the full inventory, then sequentially create missing resource pairs.

    Args:
        client: Group-scoped SDK client.
        settings: Deployed defaults and named inventory.
        apply: Whether to create resources rather than only log the plan.

    Raises:
        WorkspaceError: An existing workspace or disk conflicts with the inventory.
        AzureError: A resource read or creation fails.
    """
    if not settings.workspaces:
        LOGGER.info("No workspaces declared; nothing to provision.")
        return
    volumes = {volume.name: volume for volume in client.list_volumes()}
    sandboxes = list(client.list_sandboxes())
    pending: list[WorkspaceDefinition] = []
    for workspace in settings.workspaces:
        matches = [sandbox for sandbox in sandboxes if sandbox.labels.get("name") == workspace.name]
        volume = volumes.get(workspace.volume_name)
        if matches:
            if len(matches) != 1:
                message = (
                    f"{workspace.name}: multiple sandboxes match; no provisioning was executed"
                )
                raise WorkspaceError(message)
            validate_existing_workspace(workspace, matches[0], volume)
            LOGGER.info("Keeping existing workspace %s unchanged.", workspace.name)
        elif volume is not None:
            message = f"{workspace.name}: orphaned disk exists; recover it explicitly"
            raise WorkspaceError(message)
        else:
            pending.append(workspace)
    for workspace in pending:
        LOGGER.info(
            "Create workspace %s with Data Disk %s (%s).",
            workspace.name,
            workspace.volume_name,
            workspace.disk_size,
        )
        if apply:
            _ = client.create_volume(
                workspace.volume_name,
                type="DataDisk",
                size=workspace.disk_size,
                labels={"managed-by": "crucible", "workspace": workspace.name},
            )
            create_workspace(client, settings, workspace.name, workspace.volume_name)


def validate_workspace_arguments(args: Arguments) -> None:
    """Validate workspace identifiers before any Azure requests.

    Args:
        args: Parsed action and workspace identifiers.

    Raises:
        ValueError: A name or volume argument is invalid.
    """
    if args.action != "provision" and not WORKSPACE_NAME.fullmatch(args.name):
        message = "Workspace name must be 2-32 lowercase letters, digits, or hyphens"
        raise ValueError(message)
    if args.action == "create" and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.volume
    ):
        message = "Specify the existing, workspace-specific Data Disk volume name"
        raise ValueError(message)


def run_workspace(args: Arguments) -> None:
    """Operate workspace sessions through subscription-scoped Azure credentials.

    Args:
        args: Parsed workspace action, scope, and execution selection.

    Raises:
        ValueError: A name or volume argument is invalid.
        WorkspaceError: Preflight finds conflicting resources or invalid settings.
        AzureError: Authentication or a sandbox operation fails.
        RuntimeError: A lifecycle poller reports a terminal failure.
    """
    validate_workspace_arguments(args)
    settings = load_workspace_settings(args)
    with (
        AzureCliCredential(subscription=args.subscription) as credential,
        SandboxGroupClient(
            endpoint_for_region(settings.location),
            credential,
            subscription_id=settings.subscription_id,
            resource_group=settings.resource_group,
            sandbox_group=settings.sandbox_group,
            api_version=ApiVersion.V2026_02_01_PREVIEW,
        ) as client,
    ):
        if args.action == "provision":
            provision_workspaces(client, settings, apply=args.apply)
            return
        matches = list(client.list_sandboxes(labels={"name": args.name}))
        expected = 0 if args.action == "create" else 1
        if len(matches) != expected:
            message = (
                f"Expected {expected} matching workspaces, found {len(matches)}; no changes made"
            )
            raise WorkspaceError(message)
        LOGGER.info("%s workspace %s.", args.action.capitalize(), args.name)
        if args.action == "create":
            volume = client.get_volume(args.volume)
            if volume.type != "DataDisk" or volume.is_attached is not False:
                message = "Creation requires an unattached dedicated Data Disk; inspect the volume"
                raise WorkspaceError(message)
            if args.apply:
                create_workspace(client, settings, args.name, args.volume)
        elif args.action == "auto-suspend":
            configure_auto_suspend(
                client.get_sandbox_client(matches[0].id),
                enabled=args.auto_suspend_enabled,
                apply=args.apply,
            )
        elif args.apply:
            sandbox = client.get_sandbox_client(matches[0].id)
            if args.action == "bootstrap":
                bootstrap_tools(sandbox)
                return
            poller = cast(
                "LROPoller[Sandbox]",
                sandbox.begin_stop() if args.action == "stop" else sandbox.begin_resume(),
            )
            _ = poller.result()


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
        ("provision", "Create missing declared workspaces and dedicated Data Disks"),
        ("create", "Create a sandbox with an existing Data Disk"),
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
            _ = command.add_argument("--volume", required=True, help="Existing dedicated Data Disk")
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
