# Validation

[Back to overview](../README.md)

## Test private service access

Use a BYO-backed workspace from the [storage example](../infra/parameters/storage.example.bicepparam)
and the normal [deployment flow](infrastructure.md#deploy). Its Storage account,
private endpoint, and DNS come from the shared provisioning modules.

Find the account and container in the deployment's
`workspaceSettings.workspaces[].storage.storageContainerResourceId`.
The user running this test needs Blob Data Reader access to that container;
normal provisioning grants the group identity access, not your personal account.

Connect through VS Code, then run in its remote terminal:

```bash
az login --use-device-code
getent hosts STORAGE_ACCOUNT.blob.core.windows.net
az storage blob list --subscription SUBSCRIPTION \
  --account-name STORAGE_ACCOUNT --container-name CONTAINER_NAME \
  --auth-mode login --output json
```

DNS must resolve to the private endpoint's IP, and listing must succeed
(`[]` for an empty container). Repeat the listing from a laptop without private network
access, using the same identity: it should fail with a Storage network denial.
A DNS result or `403` alone doesn't prove private access.

If the sandbox enforces an egress allowlist, include the storage account's blob
hostname. This test proves Blob HTTPS access only, not connectivity to every
private service or protocol.

Validate Blob mounts, home-directory bootstrap, read/write access, and persistence
in Azure before customer use. Local checks don't prove service acceptance.

Private endpoints incur charges. Use a disposable environment for destructive
tests; don't delete shared storage, developer volumes, or the shared endpoint
subnet as test cleanup.

## Validate customer readiness

Before customer use, check:

- Image startup, tool setup, and disk ownership.
- Private DNS, access to private services, and egress restrictions.
- Editor and agent connections, access boundaries, and persistence after suspend/resume.
- Time to a usable workspace and ongoing costs.
