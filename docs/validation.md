# Validation

[Back to overview](../README.md)

## Test private service access

The [private Storage test](../infra/tests/private-storage.bicep) creates a
private-only storage account and test container. It references the existing
sandbox VNet and adds a separate, non-delegated endpoint subnet, private DNS,
and container-scoped Blob Data Reader access for the supplied user. Deploy it
separately from the main template; review the subnet range and existing DNS zones
before applying it.

Connect through VS Code, then run in its remote terminal:

```bash
az login --use-device-code
getent hosts STORAGE_ACCOUNT.blob.core.windows.net
az storage blob list --subscription SUBSCRIPTION \
  --account-name STORAGE_ACCOUNT --container-name connectivity-test \
  --auth-mode login --output json
```

DNS must resolve to the private endpoint's IP, and listing the empty container
must succeed with `[]`. Repeat the listing from a laptop without private network
access, using the same identity: it should fail with a Storage network denial.
A DNS result or `403` alone doesn't prove private access.

If the sandbox enforces an egress allowlist, include the storage account's blob
hostname. This test proves Blob HTTPS access only, not connectivity to every
private service or protocol.

The live PoC passed an authenticated container listing through the sandbox and
rejected the same credential over the local public path. Repeat through your
VS Code terminal to verify your editor connection end to end.

Private endpoints incur charges. After testing, remove only the test endpoint,
storage account, test DNS resources, and endpoint subnet; keep the sandbox
group, developer disks, original subnet, and NSG.

## Validate customer readiness

Before customer use, check:

- Image startup, tool setup, and disk ownership.
- Private DNS, access to private services, and egress restrictions.
- Editor and agent connections, access boundaries, and persistence after suspend/resume.
- Time to a usable workspace and ongoing costs.
