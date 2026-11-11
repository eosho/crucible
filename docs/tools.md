# Workspace tools

[Back to overview](../README.md)

Use Microsoft's public `azure-dev` image for Node, Python, Azure CLI, Git, and
VS Code CLI. You don't need to build an image or run a private registry.

```bicep
param workspaceImage = 'azure-dev'
param workspaceBootstrapTools = true
```

When bootstrap is enabled, it installs missing tools on new workspaces. Run the
command below to set up an existing workspace.

## What bootstrap does

- Uploads the [setup scripts and pins](../src/crucible/assets/) through the SDK.
  Tool setup doesn't need a repository clone or customer credentials.
- Keeps tools already on `PATH`. Downloads for missing VS Code CLI, APM, and prek
  use the architecture-specific versions and SHA-256 checksums in
  [tools.lock.json](../src/crucible/assets/tools.lock.json).
- Installs the missing tools and tunnel helper under `/workspaces/.crucible`.
- Adds one source line to the execution user's `.profile` and `.bashrc`, preserving
  existing contents. Open a new shell or source `/workspaces/.crucible/env.sh`.
- Does not initialize projects, install their dependencies, or accept licenses.

Microsoft manages the base-tool versions, which may differ from the pins.
Bootstrap leaves installed versions alone. Allow HTTPS egress to the pinned
public download sources; setup reports a failure if it can't install a tool.

## Bootstrap an existing workspace

Deploy current Bicep so the CLI can read its settings. Resume the workspace to
Running, then review the bootstrap plan:

```bash
uv run crucible workspace bootstrap --subscription SUBSCRIPTION \
  --resource-group RESOURCE_GROUP --deployment crucible --name alice-dev
# Repeat with --apply.
```

Bootstrap won't start a stopped workspace. Tool files persist on the Data Disk.
If you recreate the sandbox, rerun setup to configure its shell.

A live test on an amd64 `azure-dev` sandbox verified APM and prek installation,
the persistent mount, and shell setup for the SDK execution user (`root`).
Automatic setup when creating a new workspace still needs a live test.

You can select other Microsoft public images by name, such as `copilot`.
Selecting the Copilot image doesn't configure GitHub authentication or run the
portal preset's full setup. Enable bootstrap only on images with Bash, `jq`,
`curl`, `sha256sum`, GNU tar, and the standard filesystem utilities.
