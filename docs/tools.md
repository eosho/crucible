# Workspace tools

[Back to overview](../README.md)

Use Microsoft's public `azure-dev` image for Node, Python, Azure CLI, Git, and
VS Code CLI. You don't need to build an image or run a private registry.

```bicep
param workspaceImage = 'azure-dev'
param workspaceBootstrapTools = true
```

Bootstrap installs missing tools on new workspaces when enabled. Use the command
below for an existing workspace. All three storage types are supported, including
read-only Blob mounts. The execution user's home directory must be writable and
allow tool execution.

## What bootstrap does

- Uploads the [setup scripts and pins](../src/crucible/assets/) through the SDK.
  Tool setup doesn't need a repository clone or customer credentials.
- Keeps tools already on `PATH`. Downloads for missing VS Code CLI, APM, and prek
  use the architecture-specific versions and SHA-256 checksums in
  [tools.lock.json](../src/crucible/assets/tools.lock.json).
- Installs missing tools under `$HOME/.local/share/crucible` on the sandbox root
  filesystem, separate from `/workspaces`.
- Adds one source line to the execution user's `.profile` and `.bashrc`, preserving
  existing contents. Open a new shell or run
  `source "$HOME/.local/share/crucible/env.sh"`.
- Does not initialize projects, install their dependencies, or accept licenses.

Microsoft manages the base-tool versions, which may differ from the pins.
Bootstrap leaves installed versions alone. Allow HTTPS egress to the pinned
public download sources; setup reports a failure if it can't install a tool.

## Bootstrap an existing workspace

The CLI reads settings from the subscription deployment. Resume the workspace to
Running, then review the bootstrap plan:

```bash
uv run crucible workspace bootstrap --subscription SUBSCRIPTION \
  --resource-group RESOURCE_GROUP --deployment crucible --name alice-dev
# Repeat with --apply.
```

Bootstrap won't start a stopped workspace. Tools live in the execution user's
home, not on the project volume. Disk-mode stop/resume preserves them; rerun setup
if you recreate the sandbox.

You can select other Microsoft public images by name, such as `copilot`.
Selecting the Copilot image doesn't configure GitHub authentication or run the
portal preset's full setup. Enable bootstrap only on images with Bash, `jq`,
`curl`, `sha256sum`, GNU tar, and the standard filesystem utilities.
