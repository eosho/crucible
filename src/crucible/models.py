"""Typed CLI arguments and deployment settings."""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeGuard

WORKSPACE_NAME = re.compile(r"[a-z][a-z0-9-]{0,30}[a-z0-9]")
STORAGE_CONTAINER_ID = re.compile(
    r"/subscriptions/[0-9a-f-]{36}/resourceGroups/[^/]+/providers/Microsoft\.Storage/storageAccounts/[a-z0-9]{3,24}/blobServices/default/containers/[a-z0-9][a-z0-9-]{1,61}[a-z0-9]",
    re.IGNORECASE,
)


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
    read_only: bool


def is_record(value: object) -> TypeGuard[dict[str, object]]:
    """Check whether a decoded JSON value is an object."""
    return isinstance(value, dict)


def is_array(value: object) -> TypeGuard[list[object]]:
    """Check whether a decoded JSON value is an array."""
    return isinstance(value, list)


@dataclass(frozen=True)
class WorkspaceStorage:
    """Validated storage backing the /workspaces mount.

    Attributes:
        type: SDK volume type.
        size: Data Disk allocation; omitted for Blob storage.
        read_only: Whether the Blob mount is read-only.
        storage_container_resource_id: Existing container ARM ID for BYO Blob.
    """

    type: Literal["DataDisk", "AzureBlob", "AzureBlobByo"]
    size: str | None = None
    read_only: bool = False
    storage_container_resource_id: str | None = None

    @classmethod
    def from_outputs(cls, value: object) -> "WorkspaceStorage":
        """Parse one discriminated storage definition.

        Args:
            value: Deployed storage object.

        Returns:
            Storage configuration with no credentials.

        Raises:
            ValueError: The type, required fields, size, or container ID is invalid.
        """
        if not is_record(value):
            message = "Workspace storage must be a DataDisk, AzureBlob, or AzureBlobByo object"
            raise ValueError(message)
        volume_type = value.get("type")
        if volume_type == "DataDisk":
            size = value.get("size")
            if (
                set(value) != {"type", "size"}
                or not isinstance(size, str)
                or not re.fullmatch(r"[1-9][0-9]*(Mi|Gi)", size)
            ):
                message = "DataDisk storage requires only type and a positive size such as 20Gi"
                raise ValueError(message)
            return cls(type="DataDisk", size=size)
        return cls.parse_blob(value)

    @classmethod
    def parse_blob(cls, value: dict[str, object]) -> "WorkspaceStorage":
        """Parse managed or customer-owned Blob storage.

        Args:
            value: Storage object containing an explicit readOnly selection.

        Returns:
            Blob storage configuration.

        Raises:
            ValueError: The type, fields, access selection, or container ID is invalid.
        """
        read_only = value.get("readOnly")
        if type(read_only) is not bool:
            message = "Blob storage requires a boolean readOnly field"
            raise ValueError(message)
        if value.get("type") == "AzureBlob" and set(value) == {"type", "readOnly"}:
            return cls(type="AzureBlob", read_only=read_only)
        container = value.get("storageContainerResourceId")
        if (
            value.get("type") != "AzureBlobByo"
            or set(value) != {"type", "readOnly", "storageContainerResourceId"}
            or not isinstance(container, str)
            or not STORAGE_CONTAINER_ID.fullmatch(container)
        ):
            message = "AzureBlobByo requires type, readOnly, and a valid storageContainerResourceId"
            raise ValueError(message)
        return cls(
            type="AzureBlobByo", read_only=read_only, storage_container_resource_id=container
        )


@dataclass(frozen=True)
class WorkspaceDefinition:
    """A stable workspace name and its persistent storage allocation.

    Attributes:
        name: Unique name used for workspace labels and volume naming.
        storage: Configuration of the /workspaces volume.
    """

    name: str
    storage: WorkspaceStorage

    @property
    def volume_name(self) -> str:
        """Return the deterministic name of this workspace's volume."""
        return f"{self.name}-data"

    @classmethod
    def from_outputs(cls, value: object) -> "WorkspaceDefinition":
        """Parse one named workspace from deployment outputs.

        Args:
            value: Decoded workspace definition.

        Returns:
            The immutable workspace name and storage allocation.

        Raises:
            TypeError: The definition is not an object.
            ValueError: The definition has an invalid name or storage configuration.
        """
        if not is_record(value):
            message = "Each deployed workspace must be an object with name and storage"
            raise TypeError(message)
        name = value.get("name")
        if not isinstance(name, str) or not WORKSPACE_NAME.fullmatch(name):
            message = "Workspace names must be 2-32 lowercase letters, digits, or hyphens"
            raise ValueError(message)
        if set(value) != {"name", "storage"}:
            message = f"Workspace {name} requires only name and storage fields"
            raise ValueError(message)
        return cls(name=name, storage=WorkspaceStorage.from_outputs(value["storage"]))


def parse_workspaces(value: object) -> tuple[WorkspaceDefinition, ...]:
    """Validate the deployment's named inventory before any resource changes.

    Args:
        value: Decoded workspace array from the deployment output.

    Returns:
        Unique workspace definitions in declared order.

    Raises:
        TypeError: The inventory is not an array or contains a non-object entry.
        ValueError: A definition is invalid or workspace names are duplicated.
    """
    if not is_array(value):
        message = "Deployment workspaceSettings requires a workspaces array; redeploy Bicep"
        raise TypeError(message)
    workspaces = tuple(WorkspaceDefinition.from_outputs(entry) for entry in value)
    if len({workspace.name for workspace in workspaces}) != len(workspaces):
        message = "Deployed workspace names must be unique"
        raise ValueError(message)
    return workspaces


def validate_storage_configuration(
    workspaces: tuple[WorkspaceDefinition, ...], *, identity_enabled: bool
) -> None:
    """Require a group identity before creating customer-owned Blob volumes.

    Args:
        workspaces: Parsed workspace inventory.
        identity_enabled: Whether Bicep enables the group storage identity.

    Raises:
        ValueError: BYO storage without an identity is requested.
    """
    for workspace in workspaces:
        if workspace.storage.type == "AzureBlobByo" and not identity_enabled:
            message = "BYO Blob requires the deployed storage identity; redeploy current Bicep"
            raise ValueError(message)


@dataclass(frozen=True)
class WorkspaceSettings:
    """Non-secret session defaults read from one explicitly scoped deployment.

    Attributes:
        subscription_id: Subscription containing the sandbox group.
        resource_group: Resource group containing the sandbox group.
        location: Azure region used for data-plane requests.
        sandbox_group: Name of the provisioned sandbox group.
        vnet_connection: Group connection used to reach the customer network.
        cpu: CPU allocation for new sandbox sessions, in millicores.
        memory: Memory allocation for new sandbox sessions.
        root_disk_size: Root disk allocation for new sandbox sessions.
        idle_timeout_seconds: Inactivity interval used when auto-suspend is enabled.
        auto_suspend_enabled: Whether new workspaces suspend automatically when idle.
        image: Microsoft-provided sandbox image name.
        workspaces: Named inventory used only for additive provisioning.
        bootstrap_tools: Whether to set up missing tools on new workspaces.
        storage_identity_enabled: Whether the group has a system-assigned storage identity.
    """

    subscription_id: str
    resource_group: str
    location: str
    sandbox_group: str
    vnet_connection: str
    cpu: str
    memory: str
    root_disk_size: str
    idle_timeout_seconds: int
    auto_suspend_enabled: bool
    image: str
    workspaces: tuple[WorkspaceDefinition, ...]
    bootstrap_tools: bool
    storage_identity_enabled: bool

    @classmethod
    def from_outputs(
        cls, value: object, *, subscription_id: str, resource_group: str
    ) -> "WorkspaceSettings":
        """Parse deployment outputs without duplicating Azure resource validation.

        Args:
            value: Decoded ``workspaceSettings`` deployment output.
            subscription_id: Explicit subscription scope supplied by the caller.
            resource_group: Explicit resource group supplied by the caller.

        Returns:
            Immutable session settings using the supplied Azure scope.

        Raises:
            TypeError: The output is not an object.
            ValueError: A required field is missing or invalid.
        """
        if not is_record(value):
            message = "Deployment has no workspaceSettings object; deploy the current Bicep first"
            raise TypeError(message)
        settings: dict[str, str] = {}
        for field in (
            "location",
            "sandboxGroup",
            "vnetConnection",
            "cpu",
            "memory",
            "rootDiskSize",
            "image",
        ):
            setting = value.get(field)
            if not isinstance(setting, str) or not setting:
                message = f"Deployment workspaceSettings is missing a nonempty {field}"
                raise ValueError(message)
            settings[field] = setting
        idle_timeout = value.get("idleTimeoutSeconds")
        if type(idle_timeout) is not int or idle_timeout < 1:
            message = (
                "Deployment workspaceSettings requires a positive integer idleTimeoutSeconds; "
                "redeploy the current Bicep"
            )
            raise ValueError(message)
        bootstrap = value.get("bootstrapTools")
        if not isinstance(bootstrap, bool):
            message = "Deployment workspaceSettings requires bootstrapTools; redeploy current Bicep"
            raise TypeError(message)
        auto_suspend = value.get("autoSuspendEnabled")
        if not isinstance(auto_suspend, bool):
            message = (
                "Deployment workspaceSettings requires autoSuspendEnabled; redeploy current Bicep"
            )
            raise TypeError(message)
        storage_identity = value.get("storageIdentityEnabled")
        if type(storage_identity) is not bool:
            message = "Deployment workspaceSettings requires storageIdentityEnabled; redeploy Bicep"
            raise TypeError(message)
        workspaces = parse_workspaces(value.get("workspaces"))
        validate_storage_configuration(workspaces, identity_enabled=storage_identity)
        return cls(
            subscription_id=subscription_id,
            resource_group=resource_group,
            location=settings["location"],
            sandbox_group=settings["sandboxGroup"],
            vnet_connection=settings["vnetConnection"],
            cpu=settings["cpu"],
            memory=settings["memory"],
            root_disk_size=settings["rootDiskSize"],
            idle_timeout_seconds=idle_timeout,
            auto_suspend_enabled=auto_suspend,
            image=settings["image"],
            workspaces=workspaces,
            bootstrap_tools=bootstrap,
            storage_identity_enabled=storage_identity,
        )
