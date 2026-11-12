"""Deploy Bicep and read infrastructure settings through Azure CLI."""

import json
import logging
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from ipaddress import IPv4Network
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from .models import Arguments, WorkspaceError, WorkspaceSettings, is_array, is_record

LOGGER = logging.getLogger(__name__)


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
    """Plan, preview, or deploy ARM infrastructure.

    Args:
        args: Subscription, target group, deployment name, and Bicep parameters.

    Raises:
        ValueError: The Bicep parameter file or its explicit location is invalid.
        subprocess.CalledProcessError: Azure CLI rejects the deployment.
        WorkspaceError: Group existence or compiled parameters are incompatible.
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
    validate_deployment_storage(template, inputs)
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


def validate_deployment_storage(template: str, inputs: dict[str, object]) -> None:
    """Validate storage inputs before ARM can attach identities or grant access.

    Args:
        template: Compiled Bicep template JSON.
        inputs: Compiled parameter values.

    Raises:
        WorkspaceError: The template or parameter shapes are incompatible.
        ValueError: Workspace storage or network settings conflict.
    """
    parameters = read_json_object(template).get("parameters")
    if not is_record(parameters):
        message = "Compiled Bicep template requires parameter definitions"
        raise WorkspaceError(message)
    values: dict[str, object] = {}
    for name in ("workspaces", "workspaceBlobStorage", "network"):
        definition = parameters.get(name)
        supplied = inputs.get(name)
        if supplied is not None and is_record(supplied) and "value" in supplied:
            values[name] = supplied["value"]
        elif (
            supplied is None
            and is_record(definition)
            and ("defaultValue" in definition or definition.get("nullable") is True)
        ):
            values[name] = definition.get("defaultValue")
        else:
            message = f"Cannot read compiled Bicep parameter {name}"
            raise WorkspaceError(message)
    workspaces = values["workspaces"]
    if not is_array(workspaces):
        message = "Workspaces must be an array"
        raise ValueError(message)
    requires_endpoint = values["workspaceBlobStorage"] is not None
    for workspace in workspaces:
        storage = workspace.get("storage") if is_record(workspace) else None
        if not is_record(storage):
            message = "Each workspace requires a storage object"
            raise ValueError(message)
        requires_endpoint = requires_endpoint or storage.get("type") == "AzureBlobByo"
    validate_private_endpoint_network(values["network"], required=requires_endpoint)


def validate_private_endpoint_network(value: object, *, required: bool) -> None:
    """Check that the optional private-endpoint subnet is separate and inside a new VNet.

    Args:
        value: Compiled network configuration.
        required: Whether private Blob storage needs the subnet.

    Raises:
        ValueError: The subnet fields are incomplete, overlap, or fall outside the VNet.
    """
    if not is_record(value):
        message = "Deployment requires a network object"
        raise ValueError(message)
    name = value.get("privateEndpointSubnetName")
    prefix = value.get("privateEndpointSubnetAddressPrefix")
    if name is None and prefix is None and not required:
        return
    if not isinstance(name, str) or not name or not isinstance(prefix, str) or not prefix:
        message = "Set network.privateEndpointSubnetName and privateEndpointSubnetAddressPrefix"
        raise ValueError(message)
    endpoint = IPv4Network(prefix)
    if value.get("mode") == "newVnet":
        address_prefix = value.get("addressPrefix")
        if not isinstance(address_prefix, str) or not endpoint.subnet_of(
            IPv4Network(address_prefix)
        ):
            message = "Private-endpoint subnet must be inside network.addressPrefix"
            raise ValueError(message)
    aca_name = value.get("acaSubnetName")
    aca_prefix = value.get("acaSubnetAddressPrefix")
    if value.get("mode") == "existingSubnet":
        subnet_id = value.get("subnetId")
        aca_name = subnet_id.rsplit("/", 1)[-1] if isinstance(subnet_id, str) else None
    if name == aca_name:
        message = "Private endpoints require a separate, non-delegated subnet"
        raise ValueError(message)
    if isinstance(aca_prefix, str) and endpoint.overlaps(IPv4Network(aca_prefix)):
        message = "Private-endpoint and ACA subnet ranges must not overlap"
        raise ValueError(message)


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
