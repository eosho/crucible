"""Provision workspace volumes and operate sandbox sessions through the SDK."""

import logging
import re
from dataclasses import replace
from typing import cast

from azure.containerapps.sandbox import (
    ApiVersion,
    AutoDeletePolicy,
    AutoSuspendPolicy,
    AzureBlobByoManagedIdentityAuth,
    LifecyclePolicy,
    Sandbox,
    SandboxClient,
    SandboxGroupClient,
    SandboxVolume,
    Volume,
    endpoint_for_region,
)
from azure.core.polling import LROPoller
from azure.identity import AzureCliCredential

from .bootstrap import bootstrap_tools
from .deployment import load_workspace_settings
from .models import (
    WORKSPACE_NAME,
    Arguments,
    VolumeType,
    WorkspaceDefinition,
    WorkspaceError,
    WorkspaceSettings,
)

LOGGER = logging.getLogger(__name__)


def create_workspace(
    client: SandboxGroupClient,
    settings: WorkspaceSettings,
    name: str,
    volume: str,
    *,
    read_only: bool = False,
) -> None:
    """Create one sandbox with its volume mounted and deletion disabled.

    Args:
        client: Group-scoped SDK client.
        settings: Deployed image, sizing, network, and lifecycle defaults.
        name: Unique workspace label.
        volume: Existing volume name.
        read_only: Whether to mount the volume read-only.

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
            volumes=[
                SandboxVolume(volume_name=volume, mountpoint="/workspaces", read_only=read_only)
            ],
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
        volume: Its workspace volume, if present.

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
        volume.type not in VolumeType
        or volume.is_attached is not True
        or sandbox.state not in ("Running", "Stopped")
        or policy is None
        or (
            volume.type == VolumeType.DATA_DISK
            and (policy.auto_suspend is None or policy.auto_suspend.mode != "Disk")
        )
        or policy.auto_delete is None
        or policy.auto_delete.enabled
    ):
        message = f"{workspace.name}: partial or transitional workspace; recover it explicitly"
        raise WorkspaceError(message)


def create_workspace_volume(client: SandboxGroupClient, workspace: WorkspaceDefinition) -> None:
    """Create the declared volume without adopting or replacing existing storage.

    Args:
        client: Group-scoped SDK client.
        workspace: Preflighted workspace with its storage configuration.

    Raises:
        AzureError: The volume cannot be created or its identity lacks access.
        ValueError: The SDK rejects the volume configuration.
    """
    storage = workspace.storage
    _ = client.create_volume(
        workspace.volume_name,
        type=storage.type.value,
        size=storage.size,
        labels={"managed-by": "crucible", "workspace": workspace.name},
        storage_container_resource_id=storage.storage_container_resource_id,
        auth=AzureBlobByoManagedIdentityAuth() if storage.type == VolumeType.BYO_BLOB else None,
    )


def validate_volume_for_creation(volume: Volume, *, read_only: bool) -> None:
    """Require usable existing storage before a manual sandbox creation.

    Args:
        volume: Existing volume inspected without changing attachment state.
        read_only: Whether the requested mount is read-only.

    Raises:
        WorkspaceError: Storage is unsupported or an exclusive disk is attached or read-only.
    """
    if volume.type not in VolumeType:
        message = "Creation requires a DataDisk, AzureBlob, or AzureBlobByo volume"
        raise WorkspaceError(message)
    if volume.type == VolumeType.DATA_DISK and (volume.is_attached is not False or read_only):
        message = "DataDisk creation requires an unattached, writable dedicated disk"
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
            message = f"{workspace.name}: orphaned volume exists; recover it explicitly"
            raise WorkspaceError(message)
        else:
            pending.append(workspace)
    for workspace in pending:
        LOGGER.info(
            "Create workspace %s with %s volume %s.",
            workspace.name,
            workspace.storage.type,
            workspace.volume_name,
        )
        if apply:
            create_workspace_volume(client, workspace)
            create_workspace(
                client,
                settings,
                workspace.name,
                workspace.volume_name,
                read_only=workspace.storage.read_only,
            )


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
        message = "Specify an existing volume name"
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
            validate_volume_for_creation(volume, read_only=args.read_only)
            if args.apply:
                create_workspace(client, settings, args.name, args.volume, read_only=args.read_only)
        elif args.action == "auto-suspend":
            configure_auto_suspend(
                client.get_sandbox_client(matches[0].id),
                enabled=args.auto_suspend_enabled,
                apply=args.apply,
            )
        elif args.apply:
            if args.action == "bootstrap":
                bootstrap_tools(client.get_sandbox_client(matches[0].id))
                return
            sandbox = client.get_sandbox_client(matches[0].id)
            poller = cast(
                "LROPoller[Sandbox]",
                sandbox.begin_stop() if args.action == "stop" else sandbox.begin_resume(),
            )
            _ = poller.result()
