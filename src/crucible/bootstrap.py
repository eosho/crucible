"""Install optional user-space tools in an existing sandbox."""

import logging
import shlex
from pathlib import Path

from azure.containerapps.sandbox import SandboxClient

LOGGER = logging.getLogger(__name__)


def bootstrap_tools(sandbox: SandboxClient) -> None:
    """Stage and run tool setup in one shell execution.

    Args:
        sandbox: A running sandbox with a writable, executable execution-user home directory.

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
        name: (bundle / name).read_text(encoding="utf-8")
        for name in (
            "bootstrap-tools.sh",
            "install-tools.sh",
            "tools.lock.json",
        )
    }
    commands = [
        """set -eu
parent="$HOME/.local/share/crucible"
mkdir -p "$parent"
staging=$(mktemp -d "$parent/crucible-bootstrap.XXXXXXXXXX")
cleanup() {
    status=$?
    trap - EXIT
    if ! find "$staging" -depth -delete; then
        printf 'Error: Could not remove bootstrap staging directory: %s\\n' "$staging" >&2
        if [ "$status" -eq 0 ]; then status=1; fi
    fi
    exit "$status"
}
trap cleanup EXIT"""
    ]
    for name, content in payloads.items():
        commands.append(f'printf %s {shlex.quote(content)} > "$staging/{name}"')
    commands.append('bash "$staging/bootstrap-tools.sh"')
    script = "\n".join(commands)
    result = sandbox.exec(f"bash -c {shlex.quote(script)}")
    for line in result.stdout.splitlines():
        LOGGER.info("%s", line)
    for line in result.stderr.splitlines():
        LOGGER.log(logging.ERROR if result.exit_code else logging.WARNING, "%s", line)
    if result.exit_code:
        message = f"Tool bootstrap failed with exit status {result.exit_code}; inspect the sandbox"
        raise RuntimeError(message)
