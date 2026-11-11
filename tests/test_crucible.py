"""Verify SDK provisioning safeguards without cloud access."""

import contextlib
import io
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from azure.containerapps.sandbox import (
    ApiVersion,
    AutoDeletePolicy,
    AutoSuspendPolicy,
    ExecResult,
    LifecyclePolicy,
    Sandbox,
    SandboxClient,
    SandboxGroupClient,
    Volume,
)
from azure.core.exceptions import AzureError, HttpResponseError

from crucible import cli
from crucible.bootstrap import bootstrap_tools


class CliTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(cli.shutil, "which", return_value="/tools/az"))
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
            "resourceGroup": "test-group",
            "location": "eastus", "sandboxGroup": "test-sandboxes",
            "vnetConnection": "test-network", "cpu": "3000m", "memory": "4096Mi",
            "rootDiskSize": "40Gi", "idleTimeoutSeconds": 900,
            "autoSuspendEnabled": True,
            "image": "azure-dev", "workspaces": [],
            "bootstrapTools": False,
        }
        self.group_exists = True
        self.compiled_inputs = {"location": {"value": "eastus"}}
        self.deployed_inputs = None
        self.run = self.enterContext(patch.object(
            cli.subprocess, "run",
            side_effect=self.azure_response,
        ))
        self.credential = self.enterContext(patch.object(cli, "AzureCliCredential"))
        self.constructor = self.enterContext(patch.object(cli, "SandboxGroupClient"))
        self.client = MagicMock(spec=SandboxGroupClient)
        self.constructor.return_value.__enter__.return_value = self.client
        self.client.list_sandboxes.return_value = []
        self.client.list_volumes.return_value = []
        self.client.get_volume.return_value = Volume(type="DataDisk", is_attached=False)
        self.sandbox = MagicMock(spec=SandboxClient)
        self.client.begin_create_sandbox.return_value.result.return_value = self.sandbox
        self.client.get_sandbox_client.return_value = self.sandbox

    def azure_response(self, command, **kwargs):
        if command[1:3] == ["bicep", "build-params"]:
            value = {
                "templateJson": json.dumps({"$schema": "subscriptionDeploymentTemplate.json"}),
                "parametersJson": json.dumps({"parameters": self.compiled_inputs}),
            }
        elif command[1:3] == ["group", "exists"]:
            value = self.group_exists
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
        self.sandbox.set_lifecycle_policy.assert_not_called()
        self.sandbox.begin_stop.assert_not_called()
        self.sandbox.begin_resume.assert_not_called()

    def declare_workspaces(self):
        self.settings["workspaces"] = [
            {"name": "alice-dev", "diskSize": "20Gi"},
            {"name": "bob-dev", "diskSize": "30Gi"},
        ]

    def test_new_workspaces_bootstrap_only_when_configured(self):
        self.settings["bootstrapTools"] = True
        self.declare_workspaces()
        with patch.object(cli, "bootstrap_tools") as bootstrap:
            self.assertEqual(self.invoke("workspace provision"), 0)
            bootstrap.assert_not_called()
            self.assertEqual(self.invoke("workspace provision", "--apply"), 0)
            self.assertEqual(bootstrap.call_count, 2)

    def test_existing_workspace_bootstrap_is_explicit_and_requires_running(self):
        self.client.list_sandboxes.return_value = [Sandbox(id="existing", state="Stopped")]
        self.sandbox.get.return_value = Sandbox(id="existing", state="Stopped")
        with patch.object(cli, "bootstrap_tools") as bootstrap:
            self.assertEqual(self.invoke("workspace bootstrap", "--name", "test-dev"), 0)
            bootstrap.assert_not_called()
        self.assertEqual(self.invoke("workspace bootstrap", "--name", "test-dev", "--apply"), 1)
        self.sandbox.write_file.assert_not_called()
        self.sandbox.exec.assert_not_called()
        with patch.object(cli, "bootstrap_tools") as bootstrap:
            self.sandbox.get.return_value = Sandbox(id="existing", state="Running")
            self.assertEqual(self.invoke("workspace bootstrap", "--name", "test-dev", "--apply"), 0)
            bootstrap.assert_called_once_with(self.sandbox)
        self.sandbox.begin_resume.assert_not_called()
        self.client.begin_create_sandbox.assert_not_called()

    def test_sdk_bootstrap_uploads_assets_and_cleans_only_its_staging_directory(self):
        staging = "/tmp/crucible-bootstrap.ABCD1234"
        self.sandbox.get.return_value = Sandbox(state="Running")
        self.sandbox.exec.side_effect = [
            ExecResult(stdout=staging + "\n"),
            ExecResult(stdout="Tools ready\n"),
        ]
        bootstrap_tools(self.sandbox)
        self.assertEqual(self.sandbox.write_file.call_count, 4)
        self.sandbox.exec.assert_any_call(f"bash {staging}/bootstrap-tools.sh")
        self.sandbox.delete_file.assert_called_once_with(staging, recursive=True)
        self.assertTrue(all(call.args[0].startswith(staging + "/")
                            for call in self.sandbox.write_file.call_args_list))

    def test_sdk_bootstrap_failure_is_reported_without_replacing_the_workspace(self):
        staging = "/tmp/crucible-bootstrap.ABCD1234"
        self.sandbox.get.return_value = Sandbox(state="Running")
        self.sandbox.exec.side_effect = [
            ExecResult(stdout=staging + "\n"),
            ExecResult(exit_code=23, stderr="Network denied"),
        ]
        with self.assertRaisesRegex(RuntimeError, "exit status 23"):
            bootstrap_tools(self.sandbox)
        self.sandbox.delete_file.assert_called_once_with(staging, recursive=True)
        self.client.begin_create_sandbox.assert_not_called()
        self.client.delete_sandbox.assert_not_called()

    def test_sdk_logging_is_errors_only_and_cli_progress_remains_visible(self):
        messages = io.StringIO()
        with contextlib.redirect_stderr(messages):
            status = cli.main([
                "deploy", *self.scope, "--parameters", str(self.parameters),
            ])
            sdk_logger = cli.logging.getLogger("azure.core.pipeline.policies.http_logging_policy")
            sdk_logger.info("SDK request details")
            sdk_logger.warning("SDK warning details")
            sdk_logger.error("SDK error details")
        self.assertEqual(status, 0)
        self.assertIn("Plan only;", messages.getvalue())
        self.assertIn("Reuse resource group", messages.getvalue())
        self.assertNotIn("SDK request details", messages.getvalue())
        self.assertNotIn("SDK warning details", messages.getvalue())
        self.assertIn("SDK error details", messages.getvalue())
        self.assertRegex(
            messages.getvalue().splitlines()[0],
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| INFO \| Plan only;",
        )
        self.assertRegex(messages.getvalue(), r"\| ERROR \| SDK error details")

    def test_deploy_plan_and_what_if_never_provision_workspaces(self):
        arguments = ["--parameters", str(self.parameters)]
        self.assertEqual(self.invoke("deploy", *arguments), 0)
        self.assertEqual(self.run.call_count, 2)
        self.assertIsNone(self.deployed_inputs)
        self.assertEqual(self.invoke("deploy", *arguments, "--what-if"), 0)
        self.assertEqual(self.run.call_args.args[0][:4], [
            "/tools/az", "deployment", "sub", "what-if",
        ])
        self.assertIs(self.run.call_args.kwargs["shell"], False)
        self.assertEqual(self.deployed_inputs["createResourceGroup"], {"value": False})
        self.assertEqual(self.deployed_inputs["resourceGroupName"], {"value": "test-group"})
        self.constructor.assert_not_called()

    def test_all_in_apply_reuses_existing_group_and_provisions_declared_workspaces(self):
        self.declare_workspaces()
        self.assertEqual(self.invoke(
            "deploy", "--parameters", str(self.parameters), "--deployment", "team-dev", "--apply"
        ), 0)
        self.assertEqual(self.deployed_inputs["createResourceGroup"], {"value": False})
        self.assertEqual(self.client.create_volume.call_count, 2)
        self.assertEqual(self.client.begin_create_sandbox.call_count, 2)
        calls = [call.args[0] for call in self.run.call_args_list]
        self.assertEqual(calls[2][:4], ["/tools/az", "deployment", "sub", "create"])
        self.assertEqual(calls[3][:4], ["/tools/az", "deployment", "sub", "show"])
        self.assertEqual(calls[2][calls[2].index("--name") + 1], "team-dev")
        self.assertEqual(calls[3][calls[3].index("--name") + 1], "team-dev")

    def test_missing_group_is_created_by_bicep_before_sdk_provisioning(self):
        self.group_exists = False
        self.declare_workspaces()
        self.assertEqual(self.invoke(
            "deploy", "--parameters", str(self.parameters), "--apply"
        ), 0)
        self.assertEqual(self.deployed_inputs["createResourceGroup"], {"value": True})
        self.assertEqual(self.client.create_volume.call_count, 2)
        self.assertFalse(any(call.args[0][1:3] == ["group", "create"]
                             for call in self.run.call_args_list))

    def test_failed_arm_deployment_never_starts_sdk_provisioning(self):
        def fail_deployment(command, **kwargs):
            if command[1:4] == ["deployment", "sub", "create"]:
                raise subprocess.CalledProcessError(1, command, stderr="Deployment failed")
            return self.azure_response(command, **kwargs)
        self.run.side_effect = fail_deployment
        self.assertEqual(self.invoke(
            "deploy", "--parameters", str(self.parameters), "--apply"
        ), 1)
        self.constructor.assert_not_called()
        self.assert_no_mutations()

    def test_missing_region_and_mismatched_deployment_scope_fail_explicitly(self):
        self.compiled_inputs = {}
        self.assertEqual(self.invoke("deploy", "--parameters", str(self.parameters), "--apply"), 2)
        self.assertIsNone(self.deployed_inputs)
        self.settings["resourceGroup"] = "another-group"
        self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
        self.constructor.assert_not_called()

    def test_plan_uses_subscription_scoped_sdk_without_mutation(self):
        self.declare_workspaces()
        self.assertEqual(self.invoke("workspace provision"), 0)
        self.credential.assert_called_once_with(subscription="test-subscription")
        self.assertEqual(self.constructor.call_args.kwargs["sandbox_group"], "test-sandboxes")
        self.assertEqual(
            self.constructor.call_args.kwargs["api_version"], ApiVersion.V2026_02_01_PREVIEW
        )
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.args[0][:4], [
            "/tools/az", "deployment", "sub", "show",
        ])
        self.assert_no_mutations()

    def test_provision_creates_dedicated_disks_and_waits_for_sandboxes(self):
        self.declare_workspaces()
        self.assertEqual(self.invoke("workspace provision", "--apply"), 0)
        self.assertEqual(self.client.create_volume.call_count, 2)
        for index, name, size in ((0, "alice-dev", "20Gi"), (1, "bob-dev", "30Gi")):
            volume = self.client.create_volume.call_args_list[index]
            self.assertEqual(volume.args, (f"{name}-data",))
            self.assertEqual(volume.kwargs["type"], "DataDisk")
            self.assertEqual(volume.kwargs["size"], size)
            create = self.client.begin_create_sandbox.call_args_list[index].kwargs
            self.assertEqual(create["labels"]["name"], name)
            self.assertEqual(create["volumes"][0].volume_name, f"{name}-data")
            self.assertEqual(create["volumes"][0].mountpoint, "/workspaces")
            self.assertEqual(create["auto_suspend_mode"], "Disk")
            self.assertEqual(create["auto_suspend_seconds"], 900)
        self.assertEqual(self.client.begin_create_sandbox.return_value.result.call_count, 2)
        policy = self.sandbox.set_lifecycle_policy.call_args.args[0]
        self.assertFalse(policy.auto_delete.enabled)
        self.assertTrue(policy.auto_suspend.enabled)
        self.assertEqual(policy.auto_suspend.mode, "Disk")

    def test_new_workspaces_can_disable_auto_suspend_without_zero_timeout(self):
        self.settings["autoSuspendEnabled"] = False
        self.declare_workspaces()
        self.assertEqual(self.invoke("workspace provision", "--apply"), 0)
        self.assertEqual(self.client.begin_create_sandbox.call_count, 2)
        self.assertEqual(self.sandbox.set_lifecycle_policy.call_count, 2)
        for creation in self.client.begin_create_sandbox.call_args_list:
            self.assertEqual(creation.kwargs["auto_suspend_seconds"], 900)
            self.assertEqual(creation.kwargs["auto_suspend_mode"], "Disk")
        for update in self.sandbox.set_lifecycle_policy.call_args_list:
            policy = update.args[0]
            self.assertFalse(policy.auto_suspend.enabled)
            self.assertFalse(policy.auto_delete.enabled)
            self.assertEqual(policy.auto_suspend.interval, 900)
            self.assertEqual(policy.auto_suspend.mode, "Disk")

    def test_existing_owned_workspace_is_never_restarted_or_updated(self):
        self.settings["autoSuspendEnabled"] = False
        self.settings["workspaces"] = [{"name": "alice-dev", "diskSize": "80Gi"}]
        labels = {"managed-by": "crucible", "workspace": "alice-dev"}
        self.client.list_volumes.return_value = [
            Volume(name="alice-dev-data", type="DataDisk", size="20Gi",
                   is_attached=True, labels=labels)
        ]
        self.client.list_sandboxes.return_value = [
            Sandbox(id="existing", state="Stopped",
                    labels=labels | {"name": "alice-dev", "volume": "alice-dev-data"},
                    lifecycle=LifecyclePolicy(
                        auto_suspend=AutoSuspendPolicy(enabled=True, mode="Disk", interval=300),
                        auto_delete=AutoDeletePolicy(enabled=False),
                    ))
        ]
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.client.list_sandboxes.return_value[0].lifecycle.auto_suspend.enabled = enabled
                self.assertEqual(self.invoke("workspace provision", "--apply"), 0)
        self.assert_no_mutations()
        self.client.get_sandbox_client.assert_not_called()

    def test_late_conflicts_abort_before_any_workspace_is_created(self):
        self.declare_workspaces()
        for volumes, sandboxes in (
            ([Volume(name="bob-dev-data", type="DataDisk", is_attached=False)], []),
            ([], [Sandbox(labels={"name": "bob-dev"})]),
            ([], [Sandbox(labels={"name": "bob-dev"})] * 2),
        ):
            with self.subTest(volumes=volumes, sandboxes=sandboxes):
                self.client.list_volumes.return_value = volumes
                self.client.list_sandboxes.return_value = sandboxes
                self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
                self.assert_no_mutations()

    def test_partial_workspace_is_not_repaired_automatically(self):
        self.settings["workspaces"] = [{"name": "alice-dev", "diskSize": "20Gi"}]
        labels = {"managed-by": "crucible", "workspace": "alice-dev"}
        for attached, policy in (
            (False, LifecyclePolicy()),
            (True, None),
        ):
            with self.subTest(attached=attached):
                self.client.list_volumes.return_value = [
                    Volume(name="alice-dev-data", type="DataDisk",
                           is_attached=attached, labels=labels)
                ]
                self.client.list_sandboxes.return_value = [
                    Sandbox(state="Running",
                            labels=labels | {"name": "alice-dev", "volume": "alice-dev-data"},
                            lifecycle=policy)
                ]
                self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
                self.assert_no_mutations()

    def test_sdk_failure_stops_batch_and_preserves_created_disk(self):
        self.declare_workspaces()
        self.client.begin_create_sandbox.return_value.result.side_effect = HttpResponseError(
            message="Sandbox provisioning failed"
        )
        self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
        self.client.create_volume.assert_called_once()
        self.client.begin_create_sandbox.assert_called_once()
        self.client.delete_volume.assert_not_called()
        self.client.delete_sandbox.assert_not_called()

    def test_invalid_outputs_stop_before_sdk_calls(self):
        for changes in (
            {"image": ""}, {"idleTimeoutSeconds": True}, {"workspaces": None},
            {"autoSuspendEnabled": None}, {"autoSuspendEnabled": "false"},
            {"autoSuspendEnabled": 0},
            {"workspaces": [{"name": "../escape", "diskSize": "20Gi"}]},
            {"workspaces": [{"name": "alice-dev", "diskSize": "20Gi"}] * 2},
        ):
            with self.subTest(changes=changes):
                original = self.settings.copy()
                self.settings.update(changes)
                self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
                self.constructor.assert_not_called()
                self.settings = original

    def test_empty_inventory_does_not_read_or_mutate_data_plane(self):
        self.assertEqual(self.invoke("workspace provision", "--apply"), 0)
        self.client.list_sandboxes.assert_not_called()
        self.client.list_volumes.assert_not_called()
        self.assert_no_mutations()

    def test_outputs_require_the_current_lifecycle_schema(self):
        del self.settings["autoSuspendEnabled"]
        self.assertEqual(self.invoke("workspace provision", "--apply"), 1)
        self.constructor.assert_not_called()

    def test_manual_create_selects_public_image_and_attaches_disk(self):
        for image in ("azure-dev", "copilot"):
            with self.subTest(image=image):
                self.settings["image"] = image
                self.assertEqual(self.invoke(
                    "workspace create", "--name", "test-dev", "--volume", "test-disk", "--apply"
                ), 0)
                create = self.client.begin_create_sandbox.call_args.kwargs
                self.assertEqual(create["disk"], image)
                self.assertNotIn("disk_id", create)
                self.assertEqual(create["cpu"], "3000m")
                self.assertEqual(create["disk_size"], "40Gi")
                self.assertEqual(create["customer_vnet_connection_name"], "test-network")
                self.assertEqual(create["volumes"][0].volume_name, "test-disk")
        self.client.create_volume.assert_not_called()

    def test_manual_create_rejects_attached_or_shared_volume(self):
        for volume in (Volume(type="DataDisk", is_attached=True), Volume(type="AzureBlob")):
            with self.subTest(volume=volume):
                self.client.get_volume.return_value = volume
                self.assertEqual(self.invoke(
                    "workspace create", "--name", "test-dev", "--volume", "test-disk", "--apply"
                ), 1)
                self.assert_no_mutations()

    def test_stop_resume_require_one_match_and_wait_for_completion(self):
        for action in ("stop", "resume"):
            for matches, expected in (([], 1), ([Sandbox()] * 2, 1), ([Sandbox(id="one")], 0)):
                with self.subTest(action=action, matches=matches):
                    self.sandbox.reset_mock()
                    self.client.list_sandboxes.return_value = matches
                    self.assertEqual(self.invoke(
                        f"workspace {action}", "--name", "test-dev", "--apply"
                    ), expected)
                    operation = getattr(self.sandbox, f"begin_{action}")
                    if expected == 0:
                        operation.return_value.result.assert_called_once()
                    else:
                        operation.assert_not_called()

    def test_stop_plan_never_resumes_or_stops_existing_workspace(self):
        self.client.list_sandboxes.return_value = [Sandbox(id="one", state="Stopped")]
        self.assertEqual(self.invoke("workspace stop", "--name", "test-dev"), 0)
        self.assert_no_mutations()

    def test_auto_suspend_plan_and_ambiguous_names_never_mutate(self):
        for flag in ("--enable", "--disable"):
            for matches, expected in (([], 1), ([Sandbox()] * 2, 1), ([Sandbox(id="one")], 0)):
                with self.subTest(flag=flag, matches=matches):
                    self.client.list_sandboxes.return_value = matches
                    self.assertEqual(self.invoke(
                        "workspace auto-suspend", "--name", "test-dev", flag,
                    ), expected)
        self.sandbox.get.assert_not_called()
        self.assert_no_mutations()

    def test_auto_suspend_toggle_preserves_other_policy_fields_without_resuming(self):
        self.client.list_sandboxes.return_value = [Sandbox(id="one", state="Stopped")]
        self.sandbox.set_lifecycle_policy.side_effect = lambda policy: policy
        for enabled, flag in ((False, "--disable"), (True, "--enable")):
            with self.subTest(enabled=enabled):
                self.sandbox.set_lifecycle_policy.reset_mock()
                original = LifecyclePolicy(
                    auto_suspend=AutoSuspendPolicy(enabled=not enabled, interval=300, mode="Disk"),
                    auto_delete=AutoDeletePolicy(enabled=True),
                )
                self.sandbox.get.return_value = Sandbox(state="Stopped", lifecycle=original)
                self.assertEqual(self.invoke(
                    "workspace auto-suspend", "--name", "test-dev", flag, "--apply",
                ), 0)
                updated = self.sandbox.set_lifecycle_policy.call_args.args[0]
                self.assertIs(updated.auto_suspend.enabled, enabled)
                self.assertEqual(updated.auto_suspend.interval, 300)
                self.assertEqual(updated.auto_suspend.mode, "Disk")
                self.assertEqual(updated.auto_delete, original.auto_delete)
                self.assertIs(original.auto_suspend.enabled, not enabled)
        self.sandbox.begin_resume.assert_not_called()
        self.sandbox.begin_stop.assert_not_called()
        self.client.begin_create_sandbox.assert_not_called()
        self.client.create_volume.assert_not_called()

    def test_auto_suspend_rejects_incomplete_or_incompatible_policies(self):
        self.client.list_sandboxes.return_value = [Sandbox(id="one")]
        for policy in (
            None, LifecyclePolicy(),
            LifecyclePolicy(auto_suspend=AutoSuspendPolicy(mode="Disk", interval=300)),
            LifecyclePolicy(auto_suspend=AutoSuspendPolicy(mode="Memory", interval=300),
                            auto_delete=AutoDeletePolicy()),
            LifecyclePolicy(auto_suspend=AutoSuspendPolicy(mode="Disk"),
                            auto_delete=AutoDeletePolicy()),
        ):
            with self.subTest(policy=policy):
                self.sandbox.get.return_value = Sandbox(lifecycle=policy)
                self.assertEqual(self.invoke(
                    "workspace auto-suspend", "--name", "test-dev", "--disable", "--apply",
                ), 1)
                self.assert_no_mutations()

    def test_auto_suspend_reports_failed_or_unconfirmed_updates(self):
        self.client.list_sandboxes.return_value = [Sandbox(id="one")]
        self.sandbox.get.return_value = Sandbox(lifecycle=LifecyclePolicy(
            auto_suspend=AutoSuspendPolicy(enabled=True, mode="Disk", interval=300),
            auto_delete=AutoDeletePolicy(),
        ))
        for result in (
            HttpResponseError(message="Policy update denied"),
            LifecyclePolicy(),
            LifecyclePolicy(auto_suspend=AutoSuspendPolicy(enabled=True)),
        ):
            with self.subTest(result=result):
                self.sandbox.set_lifecycle_policy.side_effect = (
                    result if isinstance(result, Exception) else None
                )
                self.sandbox.set_lifecycle_policy.return_value = result
                self.assertEqual(self.invoke(
                    "workspace auto-suspend", "--name", "test-dev", "--disable", "--apply",
                ), 1)
        self.sandbox.begin_resume.assert_not_called()
        self.sandbox.begin_stop.assert_not_called()

    def test_auto_suspend_requires_exactly_one_enable_or_disable_flag(self):
        for flags in ([], ["--enable", "--disable"]):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as error:
                self.invoke("workspace auto-suspend", "--name", "test-dev", *flags)
            self.assertEqual(error.exception.code, 2)
        self.run.assert_not_called()

    def test_invalid_arguments_do_not_contact_azure(self):
        self.assertEqual(self.invoke("workspace stop", "--name=../escape", "--apply"), 2)
        self.parameters.unlink()
        self.assertEqual(self.invoke("deploy", "--parameters", str(self.parameters), "--apply"), 2)
        self.run.assert_not_called()
        self.constructor.assert_not_called()

    def test_sdk_authentication_error_is_reported_without_mutation(self):
        self.client.list_sandboxes.side_effect = AzureError("Sign in to the customer subscription")
        self.assertEqual(self.invoke("workspace stop", "--name", "test-dev", "--apply"), 1)
        self.assert_no_mutations()


if __name__ == "__main__":
    unittest.main()
