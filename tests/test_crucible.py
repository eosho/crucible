"""Verify core deployment, workspace safety, bootstrap, and lifecycle behavior."""

import contextlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from azure.containerapps.sandbox import (
    AutoDeletePolicy,
    AutoSuspendPolicy,
    ExecResult,
    LifecyclePolicy,
    Sandbox,
    SandboxClient,
    SandboxGroupClient,
    Volume,
)
from azure.core.exceptions import HttpResponseError

from crucible import cli, deployment, workspaces
from crucible.bootstrap import bootstrap_tools


class CliTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(deployment.shutil, "which", return_value="/tools/az"))
        root_logger = cli.logging.getLogger()
        self.enterContext(patch.object(root_logger, "handlers", []))
        self.addCleanup(root_logger.setLevel, root_logger.level)
        azure_logger = cli.logging.getLogger("azure")
        self.addCleanup(azure_logger.setLevel, azure_logger.level)
        directory = self.enterContext(TemporaryDirectory())
        self.parameters = Path(directory) / "test settings.bicepparam"
        self.parameters.write_text("using none\n")
        self.scope = ["--subscription", "test-subscription", "--resource-group", "test-group"]
        self.settings = {
            "resourceGroup": "test-group", "location": "eastus",
            "sandboxGroup": "test-sandboxes", "vnetConnection": "test-network",
            "cpu": "3000m", "memory": "4096Mi", "rootDiskSize": "40Gi",
            "idleTimeoutSeconds": 900, "autoSuspendEnabled": True,
            "image": "azure-dev", "workspaces": [], "bootstrapTools": False,
        }
        self.container_id = (
            "/subscriptions/00000000-0000-0000-0000-000000000000"
            "/resourceGroups/customer-storage/providers/Microsoft.Storage"
            "/storageAccounts/customerdata/blobServices/default/containers/datasets"
        )
        self.storage_choices = [
            {"type": "DataDisk", "size": "20Gi"},
            {"type": "AzureBlob", "readOnly": False},
            {"type": "AzureBlobByo", "readOnly": True,
             "storageContainerResourceId": self.container_id},
        ]
        self.group_exists = True
        self.deleted_group = False
        self.deployed_inputs = None
        self.run = self.enterContext(patch.object(
            deployment.subprocess, "run", side_effect=self.azure_response,
        ))
        self.credential = self.enterContext(patch.object(workspaces, "AzureCliCredential"))
        self.constructor = self.enterContext(patch.object(workspaces, "SandboxGroupClient"))
        self.client = MagicMock(spec=SandboxGroupClient)
        self.constructor.return_value.__enter__.return_value = self.client
        self.client.list_sandboxes.return_value = []
        self.client.list_volumes.return_value = []
        self.sandbox = MagicMock(spec=SandboxClient)
        self.client.begin_create_sandbox.return_value.result.return_value = self.sandbox
        self.client.get_sandbox_client.return_value = self.sandbox

    def azure_response(self, command, **kwargs):
        if command[1:3] == ["bicep", "build-params"]:
            value = {
                "templateJson": json.dumps({"$schema": "subscriptionDeploymentTemplate.json"}),
                "parametersJson": json.dumps({"parameters": {"location": {"value": "eastus"}}}),
            }
        elif command[1:3] == ["group", "show"]:
            group_id = "/subscriptions/test-subscription/resourceGroups/test-group"
            return subprocess.CompletedProcess(command, 0, stdout=group_id + "\n")
        elif command[1:3] == ["resource", "list"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="Name          Type\nsandbox       Microsoft.App/sandboxGroups\n",
            )
        elif command[1:3] == ["group", "delete"]:
            self.deleted_group = True
            return subprocess.CompletedProcess(command, 0)
        elif command[1:3] == ["group", "exists"]:
            value = False if self.deleted_group else self.group_exists
        elif command[1:4] in (["deployment", "sub", "create"], ["deployment", "sub", "what-if"]):
            parameter_path = command[command.index("--parameters") + 1].removeprefix("@")
            self.deployed_inputs = json.loads(Path(parameter_path).read_text())["parameters"]
            return subprocess.CompletedProcess(command, 0)
        else:
            value = self.settings
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(value))

    def invoke(self, action, *arguments):
        with contextlib.redirect_stderr(io.StringIO()):
            return cli.main([*action.split(), *self.scope, *arguments])

    def assert_no_mutations(self):
        self.client.create_volume.assert_not_called()
        self.client.begin_create_sandbox.assert_not_called()
        self.client.delete_volume.assert_not_called()
        self.client.delete_sandbox.assert_not_called()
        self.sandbox.set_lifecycle_policy.assert_not_called()
        self.sandbox.begin_stop.assert_not_called()
        self.sandbox.begin_resume.assert_not_called()

    def declare_workspaces(self):
        self.settings["workspaces"] = [
            {"name": name, "storage": self.storage_choices[0]}
            for name in ("alice-dev", "bob-dev")
        ]

    def test_deploy_plan_and_what_if_never_provision_workspaces(self):
        arguments = ["--parameters", str(self.parameters)]
        self.assertEqual(self.invoke("deploy", *arguments), 0)
        self.assertIsNone(self.deployed_inputs)
        self.assertEqual(self.invoke("deploy", *arguments, "--what-if"), 0)
        self.assertEqual(self.run.call_args.args[0][:4], [
            "/tools/az", "deployment", "sub", "what-if",
        ])
        self.constructor.assert_not_called()
        self.assert_no_mutations()

    def test_apply_deploys_before_provisioning_and_handles_group_existence(self):
        self.declare_workspaces()
        for exists in (True, False):
            with self.subTest(group_exists=exists):
                self.group_exists = exists
                self.run.reset_mock()
                self.client.reset_mock()
                self.assertEqual(self.invoke(
                    "deploy", "--parameters", str(self.parameters),
                    "--deployment", "team-dev", "--apply",
                ), 0)
                self.assertEqual(self.deployed_inputs["createResourceGroup"]["value"], not exists)
                self.assertEqual(self.deployed_inputs["resourceGroupName"]["value"], "test-group")
                calls = [call.args[0] for call in self.run.call_args_list]
                self.assertEqual(calls[2][:4], ["/tools/az", "deployment", "sub", "create"])
                self.assertEqual(calls[3][:4], ["/tools/az", "deployment", "sub", "show"])
                self.assertEqual(calls[3][calls[3].index("--name") + 1], "team-dev")
                self.assertEqual(self.client.create_volume.call_count, 2)
                self.assertEqual(self.client.begin_create_sandbox.call_count, 2)

    def test_arm_failure_prevents_sdk_provisioning(self):
        def fail_deployment(command, **kwargs):
            if command[1:4] == ["deployment", "sub", "create"]:
                raise subprocess.CalledProcessError(1, command, stderr="Deployment failed")
            return self.azure_response(command, **kwargs)
        self.run.side_effect = fail_deployment
        self.assertEqual(self.invoke(
            "deploy", "--parameters", str(self.parameters), "--apply",
        ), 1)
        self.constructor.assert_not_called()
        self.assert_no_mutations()

    def test_destroy_preview_is_read_only(self):
        messages = io.StringIO()
        with contextlib.redirect_stderr(messages):
            self.assertEqual(cli.main(["destroy", *self.scope]), 0)
        self.assertIn("ALL resources and workspace data", messages.getvalue())
        self.assertIn("sandbox", messages.getvalue())
        self.assertEqual(
            [call.args[0][1:3] for call in self.run.call_args_list],
            [["group", "show"], ["resource", "list"]],
        )
        self.assertFalse(self.deleted_group)
        self.constructor.assert_not_called()
        self.assert_no_mutations()

    def test_destroy_apply_deletes_only_the_target_group_and_verifies_completion(self):
        self.assertEqual(self.invoke("destroy", "--apply"), 0)
        calls = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual([command[1:3] for command in calls], [
            ["group", "show"], ["resource", "list"],
            ["group", "delete"], ["group", "exists"],
        ])
        self.assertIn("--yes", calls[2])
        self.assertNotIn("--no-wait", calls[2])
        for command in calls:
            self.assertEqual(command[command.index("--subscription") + 1], "test-subscription")
            self.assertEqual(command[command.index("--resource-group") + 1], "test-group")
        self.assertTrue(self.deleted_group)
        self.constructor.assert_not_called()

    def test_destroy_reports_failed_or_unconfirmed_deletion(self):
        for failure in ("delete", "verification"):
            with self.subTest(failure=failure):
                self.deleted_group = False
                self.run.reset_mock()
                def azure_failure(command, **kwargs):
                    if failure == "delete" and command[1:3] == ["group", "delete"]:
                        raise subprocess.CalledProcessError(
                            1, command, stderr="Resource group is locked",
                        )
                    if failure == "verification" and command[1:3] == ["group", "exists"]:
                        return subprocess.CompletedProcess(command, 0, stdout="true")
                    return self.azure_response(command, **kwargs)
                self.run.side_effect = azure_failure
                messages = io.StringIO()
                with contextlib.redirect_stderr(messages):
                    self.assertEqual(cli.main([
                        "destroy", *self.scope, "--apply",
                    ]), 1)
                self.assertNotIn("Deleted resource group", messages.getvalue())
                if failure == "delete":
                    self.assertFalse(self.deleted_group)
                    self.assertIn("locked", messages.getvalue())
                else:
                    self.assertIn("not confirmed", messages.getvalue())
        self.constructor.assert_not_called()

    def test_scope_mismatch_and_duplicate_inventory_fail_before_sdk_calls(self):
        self.settings["resourceGroup"] = "another-group"
        self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
        self.settings["resourceGroup"] = "test-group"
        self.declare_workspaces()
        self.settings["workspaces"][1]["name"] = "alice-dev"
        self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
        self.constructor.assert_not_called()
        self.assert_no_mutations()

    def test_provision_creates_each_storage_type_with_mount_and_lifecycle_settings(self):
        for storage in self.storage_choices:
            with self.subTest(storage=storage):
                self.client.reset_mock()
                self.sandbox.reset_mock()
                self.settings["workspaces"] = [{"name": "alice-dev", "storage": storage}]
                self.settings["autoSuspendEnabled"] = storage["type"] != "DataDisk"
                self.assertEqual(self.invoke("workspace provision"), 0)
                self.assert_no_mutations()
                self.assertEqual(self.invoke("workspace provision", "--apply"), 0)
                volume = self.client.create_volume.call_args
                self.assertEqual(volume.args, ("alice-dev-data",))
                self.assertEqual(volume.kwargs["type"], storage["type"])
                self.assertIs(type(volume.kwargs["type"]), str)
                self.assertEqual(volume.kwargs["size"], storage.get("size"))
                if storage["type"] == "AzureBlobByo":
                    self.assertEqual(volume.kwargs["storage_container_resource_id"], self.container_id)
                    self.assertEqual(volume.kwargs["auth"]._to_dict(), {
                        "kind": "ManagedIdentity", "identity": {"kind": "SystemAssigned"},
                    })
                create = self.client.begin_create_sandbox.call_args.kwargs
                self.assertEqual(create["customer_vnet_connection_name"], "test-network")
                self.assertEqual(create["volumes"][0]._to_dict(), {
                    "volumeName": "alice-dev-data", "mountpoint": "/workspaces",
                    "readOnly": storage.get("readOnly", False),
                })
                self.client.begin_create_sandbox.return_value.result.assert_called_once()
                policy = self.sandbox.set_lifecycle_policy.call_args.args[0]
                self.assertEqual(policy.auto_suspend.enabled, self.settings["autoSuspendEnabled"])
                self.assertEqual(policy.auto_suspend.interval, 900)
                self.assertEqual(policy.auto_suspend.mode, "Disk")
                self.assertFalse(policy.auto_delete.enabled)
        self.credential.assert_called_with(subscription="test-subscription")

    def test_existing_workspaces_are_never_restarted_or_reconfigured(self):
        self.settings["workspaces"] = [
            {"name": "alice-dev", "storage": {"type": "DataDisk", "size": "80Gi"}}
        ]
        labels = {"managed-by": "crucible", "workspace": "alice-dev"}
        for volume_type in ("DataDisk", "AzureBlob", "AzureBlobByo"):
            with self.subTest(volume_type=volume_type):
                self.client.list_volumes.return_value = [
                    Volume(name="alice-dev-data", type=volume_type, size="20Gi",
                           is_attached=True if volume_type == "DataDisk" else None, labels=labels)
                ]
                self.client.list_sandboxes.return_value = [
                    Sandbox(id="existing", state="Stopped",
                            labels=labels | {"name": "alice-dev", "volume": "alice-dev-data"},
                            lifecycle=LifecyclePolicy(
                                auto_suspend=AutoSuspendPolicy(
                                    mode="Disk" if volume_type == "DataDisk" else "Memory",
                                ),
                                auto_delete=AutoDeletePolicy(enabled=False),
                            ))
                ]
                self.assertEqual(self.invoke("workspace provision", "--apply"), 0)
                self.assert_no_mutations()
        self.client.get_sandbox_client.assert_not_called()

    def test_inventory_conflicts_abort_the_batch_before_any_creation(self):
        self.declare_workspaces()
        labels = {"managed-by": "someone-else", "workspace": "bob-dev"}
        owned = {"managed-by": "crucible", "workspace": "bob-dev"}
        for volumes, sandboxes in (
            ([Volume(name="bob-dev-data", type="DataDisk", is_attached=False)], []),
            ([], [Sandbox(labels={"name": "bob-dev"})] * 2),
            ([Volume(name="bob-dev-data", type="DataDisk", is_attached=True, labels=labels)],
             [Sandbox(labels=labels | {"name": "bob-dev", "volume": "bob-dev-data"})]),
            ([Volume(name="bob-dev-data", type="DataDisk", is_attached=False, labels=owned)],
             [Sandbox(state="Stopped",
                      labels=owned | {"name": "bob-dev", "volume": "bob-dev-data"},
                      lifecycle=LifecyclePolicy(
                          auto_suspend=AutoSuspendPolicy(mode="Disk"),
                          auto_delete=AutoDeletePolicy(enabled=False),
                      ))]),
        ):
            with self.subTest(volumes=volumes, sandboxes=sandboxes):
                self.client.list_volumes.return_value = volumes
                self.client.list_sandboxes.return_value = sandboxes
                self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
                self.assert_no_mutations()

    def test_sdk_failure_stops_batch_without_deleting_created_storage(self):
        self.declare_workspaces()
        self.client.begin_create_sandbox.return_value.result.side_effect = HttpResponseError(
            message="Sandbox provisioning failed",
        )
        self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
        self.client.create_volume.assert_called_once()
        self.client.begin_create_sandbox.assert_called_once()
        self.client.delete_volume.assert_not_called()
        self.client.delete_sandbox.assert_not_called()

    def test_manual_create_uses_existing_volumes_without_creating_storage(self):
        for storage in self.storage_choices:
            with self.subTest(storage=storage):
                self.client.begin_create_sandbox.reset_mock()
                self.client.get_volume.return_value = Volume(
                    type=storage["type"], is_attached=storage["type"] != "DataDisk",
                )
                flags = ["--read-only"] if storage["type"] != "DataDisk" else []
                self.assertEqual(self.invoke(
                    "workspace create", "--name", "alice-dev", "--volume", "existing", *flags,
                ), 0)
                self.client.begin_create_sandbox.assert_not_called()
                self.assertEqual(self.invoke(
                    "workspace create", "--name", "alice-dev", "--volume", "existing",
                    *flags, "--apply",
                ), 0)
                create = self.client.begin_create_sandbox.call_args.kwargs
                self.assertEqual(create["disk"], "azure-dev")
                self.assertEqual(create["volumes"][0].volume_name, "existing")
                self.assertEqual(create["volumes"][0].read_only, bool(flags))
        self.client.create_volume.assert_not_called()

    def test_manual_create_rejects_attached_or_read_only_data_disks(self):
        for attached, flags in ((True, []), (False, ["--read-only"])):
            with self.subTest(attached=attached, flags=flags):
                self.client.get_volume.return_value = Volume(type="DataDisk", is_attached=attached)
                self.assertEqual(self.invoke(
                    "workspace create", "--name", "alice-dev", "--volume", "existing",
                    *flags, "--apply",
                ), 1)
                self.assert_no_mutations()

    def test_automatic_bootstrap_respects_configuration_for_all_storage(self):
        self.settings["workspaces"] = [
            {"name": name, "storage": storage}
            for name, storage in zip(("disk-dev", "blob-dev", "byo-dev"), self.storage_choices)
        ]
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.settings["bootstrapTools"] = enabled
                with patch.object(workspaces, "bootstrap_tools") as bootstrap:
                    self.assertEqual(self.invoke("workspace provision"), 0)
                    bootstrap.assert_not_called()
                    self.assertEqual(self.invoke("workspace provision", "--apply"), 0)
                    self.assertEqual(bootstrap.call_count, 3 if enabled else 0)

    def test_explicit_bootstrap_requires_apply_and_running_and_uses_one_shell_call(self):
        self.client.list_sandboxes.return_value = [Sandbox(id="existing")]
        self.sandbox.get.return_value = Sandbox(state="Stopped")
        self.assertEqual(self.invoke("workspace bootstrap", "--name", "alice-dev"), 0)
        self.assertEqual(self.invoke(
            "workspace bootstrap", "--name", "alice-dev", "--apply",
        ), 1)
        self.sandbox.exec.assert_not_called()
        self.sandbox.get.return_value = Sandbox(state="Running")
        self.sandbox.exec.return_value = ExecResult(stdout="Tools ready\n")
        self.assertEqual(self.invoke(
            "workspace bootstrap", "--name", "alice-dev", "--apply",
        ), 0)
        self.sandbox.exec.assert_called_once()
        self.sandbox.write_file.assert_not_called()
        self.sandbox.delete_file.assert_not_called()
        self.client.get_volume.assert_not_called()
        self.assert_no_mutations()

    def test_bootstrap_failure_reports_the_installer_exit_status(self):
        self.sandbox.get.return_value = Sandbox(state="Running")
        self.sandbox.exec.return_value = ExecResult(exit_code=23, stderr="Network denied")
        with self.assertRaisesRegex(RuntimeError, "exit status 23"):
            bootstrap_tools(self.sandbox)
        self.sandbox.delete_file.assert_not_called()
        self.assert_no_mutations()

    @unittest.skipUnless(Path("/bin/bash").is_file(), "Requires Bash")
    def test_bootstrap_cleanup_preserves_failure_and_deletes_only_its_staging(self):
        payloads = {
            "bootstrap-tools.sh": "printf 'installer failed\\n' >&2\nexit 23\n",
            "install-tools.sh": ":\n", "tools.lock.json": "{}\n",
        }
        self.sandbox.get.return_value = Sandbox(state="Running")
        self.sandbox.exec.return_value = ExecResult()
        with patch.object(
            Path, "read_text", autospec=True,
            side_effect=lambda path, **kwargs: payloads[path.name],
        ):
            bootstrap_tools(self.sandbox)
        command = self.sandbox.exec.call_args.args[0]
        for cleanup_failure in (False, True):
            with self.subTest(cleanup_failure=cleanup_failure), TemporaryDirectory() as directory:
                home = Path(directory) / "test home"
                tools = home / "bin"
                tools.mkdir(parents=True)
                marker = home / ".local/share/crucible/keep.txt"
                marker.parent.mkdir(parents=True)
                marker.write_text("developer data\n")
                if cleanup_failure:
                    find = tools / "find"
                    find.write_text("#!/bin/sh\nexit 17\n")
                    find.chmod(0o755)
                with subprocess.Popen(
                    ["/bin/bash", "-c", command],
                    env={"HOME": str(home), "PATH": f"{tools}:/usr/bin:/bin"},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ) as process:
                    _, stderr = process.communicate()
                    self.assertEqual(process.returncode, 23)
                self.assertIn("installer failed", stderr)
                self.assertEqual(marker.read_text(), "developer data\n")
                remaining = list(marker.parent.glob("crucible-bootstrap.*"))
                if cleanup_failure:
                    self.assertIn("Could not remove bootstrap staging directory", stderr)
                    self.assertEqual(len(remaining), 1)
                else:
                    self.assertEqual(remaining, [])

    def test_stop_resume_require_one_match_and_apply_and_wait_for_completion(self):
        for action in ("stop", "resume"):
            for matches, expected in (([], 1), ([Sandbox()] * 2, 1), ([Sandbox(id="one")], 0)):
                with self.subTest(action=action, matches=matches):
                    self.sandbox.reset_mock()
                    self.client.list_sandboxes.return_value = matches
                    if expected == 0:
                        self.assertEqual(self.invoke(f"workspace {action}", "--name", "alice-dev"), 0)
                        self.assert_no_mutations()
                    self.assertEqual(self.invoke(
                        f"workspace {action}", "--name", "alice-dev", "--apply",
                    ), expected)
                    operation = getattr(self.sandbox, f"begin_{action}")
                    if expected == 0:
                        operation.return_value.result.assert_called_once()
                    else:
                        operation.assert_not_called()

    def test_auto_suspend_preserves_the_policy_without_resuming(self):
        self.client.list_sandboxes.return_value = [Sandbox(id="one", state="Stopped")]
        self.sandbox.set_lifecycle_policy.side_effect = lambda policy: policy
        for mode, enabled, flag in (("Memory", True, "--enable"), ("Disk", False, "--disable")):
            with self.subTest(mode=mode, enabled=enabled):
                self.sandbox.reset_mock()
                original = LifecyclePolicy(
                    auto_suspend=AutoSuspendPolicy(enabled=not enabled, interval=300, mode=mode),
                    auto_delete=AutoDeletePolicy(enabled=True),
                )
                self.sandbox.get.return_value = Sandbox(state="Stopped", lifecycle=original)
                self.assertEqual(self.invoke(
                    "workspace auto-suspend", "--name", "alice-dev", flag,
                ), 0)
                self.assert_no_mutations()
                self.assertEqual(self.invoke(
                    "workspace auto-suspend", "--name", "alice-dev", flag, "--apply",
                ), 0)
                updated = self.sandbox.set_lifecycle_policy.call_args.args[0]
                self.assertIs(updated.auto_suspend.enabled, enabled)
                self.assertEqual(updated.auto_suspend.interval, 300)
                self.assertEqual(updated.auto_suspend.mode, mode)
                self.assertEqual(updated.auto_delete, original.auto_delete)
                self.sandbox.begin_resume.assert_not_called()

    def test_auto_suspend_reports_incomplete_and_unconfirmed_policies(self):
        self.client.list_sandboxes.return_value = [Sandbox(id="one")]
        self.sandbox.get.return_value = Sandbox(lifecycle=None)
        self.assertEqual(self.invoke(
            "workspace auto-suspend", "--name", "alice-dev", "--disable", "--apply",
        ), 1)
        self.sandbox.set_lifecycle_policy.assert_not_called()
        self.sandbox.get.return_value = Sandbox(lifecycle=LifecyclePolicy(
            auto_suspend=AutoSuspendPolicy(enabled=True, mode="Disk", interval=300),
            auto_delete=AutoDeletePolicy(),
        ))
        self.sandbox.set_lifecycle_policy.return_value = LifecyclePolicy()
        self.assertEqual(self.invoke(
            "workspace auto-suspend", "--name", "alice-dev", "--disable", "--apply",
        ), 1)
        self.sandbox.begin_resume.assert_not_called()

    def test_logs_include_progress_and_timestamps_but_only_sdk_errors(self):
        messages = io.StringIO()
        with contextlib.redirect_stderr(messages):
            status = cli.main(["deploy", *self.scope, "--parameters", str(self.parameters)])
            sdk_logger = cli.logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
            sdk_logger.info("SDK request details")
            sdk_logger.error("SDK error details")
        self.assertEqual(status, 0)
        self.assertIn("Reuse resource group", messages.getvalue())
        self.assertNotIn("SDK request details", messages.getvalue())
        self.assertIn("SDK error details", messages.getvalue())
        self.assertRegex(messages.getvalue(), r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| INFO \|")


if __name__ == "__main__":
    unittest.main()
