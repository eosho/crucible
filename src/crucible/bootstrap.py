"""Install optional user-space tools in an existing sandbox."""

import logging
import shlex
from pathlib import Path, PurePosixPath

from azure.containerapps.sandbox import SandboxClient

LOGGER = logging.getLogger(__name__)


def bootstrap_tools(sandbox: SandboxClient) -> None:
    """Upload and run the pinned, skip-installed tool setup.

    Args:
        sandbox: A running sandbox with its persistent disk mounted at /workspaces.

    Raises:
        OSError: A bundled bootstrap asset cannot be read.
        RuntimeError: The sandbox is not running or remote tool setup fails.
        azure.core.exceptions.AzureError: A file or execution request fails.
    """
    if sandbox.get().state != "Running":
        message = "Resume the workspace to Running before bootstrapping tools"
        raise RuntimeError(message)
    bundle = Path(__file__).with_name("assets")
    payloads = {
        name: (bundle / name).read_bytes()
        for name in (
            "bootstrap-tools.sh",
            "install-tools.sh",
            "tools.lock.json",
            "connect-tunnel.sh",
        )
    }
    created = sandbox.exec("mktemp -d -t crucible-bootstrap.XXXXXXXXXX")
    staging = created.stdout.strip()
    path = PurePosixPath(staging)
    if (
        created.exit_code
        or not path.is_absolute()
        or not path.name.startswith("crucible-bootstrap.")
        or ".." in path.parts
    ):
        message = f"Could not create a dedicated bootstrap directory: {created.stderr}"
        raise RuntimeError(message)
    try:
        for name, content in payloads.items():
            sandbox.write_file(f"{staging}/{name}", content, mode="0644")
        result = sandbox.exec(f"bash {shlex.quote(staging + '/bootstrap-tools.sh')}")
        for line in result.stdout.splitlines():
            LOGGER.info("%s", line)
        for line in result.stderr.splitlines():
            LOGGER.log(logging.ERROR if result.exit_code else logging.WARNING, "%s", line)
        if result.exit_code:
            message = (
                f"Tool bootstrap failed with exit status {result.exit_code}; inspect the sandbox"
            )
            raise RuntimeError(message)
    finally:
        sandbox.delete_file(staging, recursive=True)
