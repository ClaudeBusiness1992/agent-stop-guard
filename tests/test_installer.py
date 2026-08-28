import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


INSTALLER_PATH = Path(__file__).parent.parent / "install.py"
SPEC = importlib.util.spec_from_file_location("agent_stop_guard_installer", INSTALLER_PATH)
INSTALLER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(INSTALLER)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.home = Path(self.tempdir.name)

    def test_merges_without_overwriting_existing_hooks(self):
        settings = self.home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "existing"}]}]}}),
            encoding="utf-8",
        )
        INSTALLER.install({"claude"}, home=self.home)
        result = json.loads(settings.read_text(encoding="utf-8"))
        self.assertTrue(INSTALLER.contains_command(result, "existing"))
        self.assertTrue(INSTALLER.contains_command(result, "agent-stop-guard"))

    def test_repeated_install_is_idempotent(self):
        INSTALLER.install({"claude", "codex", "opencode"}, home=self.home)
        INSTALLER.install({"claude", "codex", "opencode"}, home=self.home)
        claude = json.loads((self.home / ".claude" / "settings.json").read_text())
        codex = json.loads((self.home / ".codex" / "hooks.json").read_text())
        self.assertEqual(
            json.dumps(claude).count("stop-open-items-guard.py"),
            1,
        )
        self.assertEqual(json.dumps(codex).count("stop-open-items-guard.py"), 1)
        self.assertTrue(
            (self.home / ".config" / "opencode" / "plugins" / "stop-open-items-guard.ts").is_file()
        )

    def test_legacy_direct_hook_is_migrated_without_duplicate(self):
        settings = self.home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps({
                "hooks": {
                    "Stop": [{
                        "hooks": [{
                            "type": "command",
                            "command": "python3 '/tmp/legacy/stop-open-items-guard.py'",
                        }]
                    }]
                }
            }),
            encoding="utf-8",
        )
        INSTALLER.install({"claude"}, home=self.home)
        result = json.loads(settings.read_text(encoding="utf-8"))
        serialized = json.dumps(result)
        self.assertNotIn("/tmp/legacy", serialized)
        self.assertEqual(serialized.count("stop-open-items-guard.py"), 1)
        self.assertIn(".local/share/agent-stop-guard", serialized)
        self.assertEqual(
            (self.home / ".claude" / "hooks" / "stop-open-items-guard.py").read_bytes(),
            (self.home / ".local" / "share" / "agent-stop-guard" / "stop-open-items-guard.py").read_bytes(),
        )

    def test_dry_run_does_not_write(self):
        actions = INSTALLER.install({"claude"}, dry_run=True, home=self.home)
        self.assertTrue(actions)
        self.assertFalse((self.home / ".claude").exists())

    def test_optional_ask_user_guard(self):
        INSTALLER.install(
            {"claude"},
            include_ask_user_guard=True,
            home=self.home,
        )
        settings = json.loads(
            (self.home / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        self.assertTrue(
            INSTALLER.contains_command(settings, "ask-user-open-items-guard.py")
        )

    def test_install_resets_managed_file_modes(self):
        INSTALLER.install({"claude", "opencode"}, home=self.home)
        hook = self.home / ".local" / "share" / "agent-stop-guard" / "stop-open-items-guard.py"
        plugin = self.home / ".config" / "opencode" / "plugins" / "stop-open-items-guard.ts"
        hook.chmod(0o777)
        plugin.chmod(0o666)
        INSTALLER.install({"claude", "opencode"}, home=self.home)
        self.assertEqual(hook.stat().st_mode & 0o777, 0o755)
        self.assertEqual(plugin.stat().st_mode & 0o777, 0o644)

    def test_install_replaces_target_symlink_without_touching_its_victim(self):
        install_root = self.home / ".local" / "share" / "agent-stop-guard"
        install_root.mkdir(parents=True)
        victim = self.home / "victim"
        victim.write_text("unchanged", encoding="utf-8")
        target = install_root / "stop-open-items-guard.py"
        os.symlink(victim, target)
        INSTALLER.install({"codex"}, home=self.home)
        self.assertFalse(target.is_symlink())
        self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
