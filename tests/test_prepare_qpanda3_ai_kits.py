"""Configuration integration tests: real JSON/TOML, ZIP and local HTTP, no client login."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare_qpanda3_ai_kits.py"
spec = importlib.util.spec_from_file_location("qpanda_setup", SCRIPT)
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


def skill_zip():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("qpanda3-programming/SKILL.md", "---\nname: qpanda3-programming\n---\nTest fixture\n")
        archive.writestr("qpanda3-programming/resources/example.txt", "fixture reference")
    return output.getvalue()


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/missing.zip":
            self.send_error(404)
            return
        content = skill_zip() if self.path == "/qpanda3-programming.zip" else b"invalid zip"
        self.send_response(200)
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


class SetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/qpanda3-programming.zip"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test_setup_", dir=ROOT / ".tmp")
        self.base = Path(self.temp.name)
        self.project = self.base / "学习营 project"
        self.home = self.base / "home"
        self.project.mkdir()
        self.home.mkdir()
        self.env = patch.dict(os.environ, {
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "QPANDA3_TEST_KEY": "fixture-secret-not-a-real-key",
        })
        self.env.start()

    def tearDown(self):
        kit.cleanup()
        self.env.stop()
        self.temp.cleanup()

    def run_main(self, *args, cwd=None):
        output = io.StringIO()
        previous = Path.cwd()
        try:
            os.chdir(cwd or self.project)
            with patch.object(sys, "argv", [str(SCRIPT), *args]), patch.object(kit, "DEFAULT_SKILL_URL", self.url), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                kit.main()
        finally:
            os.chdir(previous)
        return output.getvalue()

    def prepare(self, tool, *args):
        return self.run_main("prepare", tool, "--api-key-env", "QPANDA3_TEST_KEY", *args)

    def config(self, tool, scope="project"):
        config, skills = kit.client_paths(tool, scope)
        return config, skills, kit.load_client_config(config)

    def test_three_clients_default_to_current_project_and_do_not_save_secret(self):
        for tool, expected in (("claude", ".claude"), ("codex", ".agents"), ("codebuddy", ".codebuddy")):
            with self.subTest(tool=tool):
                logs = self.prepare(tool)
                config, skills, data = self.config(tool)
                self.assertTrue(config.is_relative_to(self.project))
                self.assertEqual(skills, self.project / expected / "skills")
                self.assertTrue((skills / "qpanda3-programming/SKILL.md").is_file())
                self.assertNotIn("fixture-secret-not-a-real-key", config.read_text(encoding="utf8") + logs)
                key = "mcp_servers" if tool == "codex" else "mcpServers"
                server = data[key]["qpanda3-runtime"]
                if tool == "codex":
                    self.assertEqual(server["env_http_headers"], {"X-Api-Key": "QPANDA3_TEST_KEY"})
                    self.assertNotIn("http_headers", server)
                else:
                    self.assertEqual(server["type"], "http")
                    self.assertEqual(server["headers"]["X-Api-Key"], "${QPANDA3_TEST_KEY}")
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse((ROOT / "scripts/.mcp.json").exists())

    def test_explicit_project_dir_and_repeat_install_preserve_other_settings(self):
        config = self.project / ".codex/config.toml"
        config.parent.mkdir()
        original = {"model": "existing-model", "mcp_servers": {"other": {"url": "https://example.invalid/mcp", "http_headers": {"X-Test": "keep"}, "enabled": False}}}
        kit.write_client_config(config, original)
        for _ in range(2):
            self.prepare("codex", "--project-dir", str(self.project), "-p")
        data = tomllib.loads(config.read_text(encoding="utf8"))
        self.assertEqual(data["model"], original["model"])
        self.assertEqual(data["mcp_servers"]["other"], original["mcp_servers"]["other"])
        self.assertEqual(len(data["mcp_servers"]), 2)
        self.assertFalse((self.project / ".codex/skills").exists())

    def test_project_dir_can_be_selected_from_elsewhere(self):
        self.run_main("prepare", "claude", "--api-key-env", "QPANDA3_TEST_KEY", "--project-dir", str(self.project), cwd=self.base)
        self.assertTrue((self.project / ".mcp.json").is_file())
        self.assertFalse((self.base / ".mcp.json").exists())

    def test_json_merge_preserves_other_server_and_codebuddy_legacy_path(self):
        config = self.project / "mcp.json"
        config.write_text('{"mcpServers":{"other":{"command":"keep"}},"custom":42}', encoding="utf8")
        self.prepare("codebuddy")
        data = json.loads(config.read_text(encoding="utf8"))
        self.assertEqual(data["mcpServers"]["other"], {"command": "keep"})
        self.assertEqual(data["custom"], 42)
        self.assertFalse((self.project / ".mcp.json").exists())

    def test_user_scope_and_uninstall_leave_project_untouched(self):
        for tool in ("claude", "codex", "codebuddy"):
            with self.subTest(tool=tool):
                self.prepare(tool)
                project_config, skills, _ = self.config(tool)
                before = project_config.read_bytes()
                self.prepare(tool, "--user")
                user_config, user_skills, _ = self.config(tool, "user")
                self.run_main("uninstall", tool, "--user")
                self.assertEqual(project_config.read_bytes(), before)
                self.assertTrue((skills / "qpanda3-programming").is_dir())
                self.assertFalse((user_skills / "qpanda3-programming").exists())
                self.assertNotIn("qpanda3-runtime", user_config.read_text(encoding="utf8"))

    def test_project_uninstall_preserves_user_scope_and_other_skill(self):
        self.prepare("codex", "--user")
        user_config, user_skills, _ = self.config("codex", "user")
        before = user_config.read_bytes()
        self.prepare("codex")
        config, skills, data = self.config("codex")
        data["mcp_servers"]["other"] = {"url": "https://example.invalid"}
        kit.write_client_config(config, data)
        (skills / "other-skill").mkdir()
        self.run_main("uninstall", "codex")
        self.assertEqual(user_config.read_bytes(), before)
        self.assertTrue((user_skills / "qpanda3-programming").is_dir())
        self.assertTrue((skills / "other-skill").is_dir())
        self.assertEqual(set(kit.load_client_config(config)["mcp_servers"]), {"other"})

    def test_claude_uninstall_removes_only_its_mcp_approval(self):
        self.prepare("claude")
        settings = self.project / ".claude/settings.local.json"
        kit.write_client_config(settings, {"enabledMcpjsonServers": ["qpanda3-runtime", "other"], "keep": True})
        self.run_main("uninstall", "claude")
        self.assertEqual(kit.load_client_config(settings), {"enabledMcpjsonServers": ["other"], "keep": True})

    def test_unknown_tool_missing_uninstall_target_and_conflicting_scope_fail(self):
        for args in (("prepare", "typo", "-k", "fake"), ("uninstall",), ("uninstall", "codex", "-p", "--user"), ("prepare", "cline", "-k", "fake")):
            with self.subTest(args=args), self.assertRaises(SystemExit) as result:
                self.run_main(*args)
            self.assertNotEqual(result.exception.code, 0)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_missing_environment_variable_writes_nothing(self):
        with patch.dict(os.environ, {"QPANDA3_TEST_KEY": ""}), self.assertRaises(SystemExit):
            self.prepare("codex")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_broken_existing_config_is_not_overwritten(self):
        for tool, relative in (("claude", ".mcp.json"), ("codex", ".codex/config.toml")):
            with self.subTest(tool=tool):
                config = self.project / relative
                config.parent.mkdir(exist_ok=True)
                config.write_text("{ invalid", encoding="utf8")
                with self.assertRaises(ValueError):
                    self.prepare(tool)
                self.assertEqual(config.read_text(encoding="utf8"), "{ invalid")

    def test_invalid_input_json_is_not_treated_as_literal_key(self):
        config = self.project / "input.json"
        config.write_text('{"mcp": []}', encoding="utf8")
        with self.assertRaises(ValueError):
            self.run_main("prepare", "codex", "-k", str(config))
        self.assertFalse((self.project / ".codex").exists())

    def test_real_http_failure_and_invalid_zip_leave_config_unwritten(self):
        for suffix in ("missing.zip", "invalid.zip"):
            with self.subTest(suffix=suffix):
                config = self.project / "input.json"
                config.write_text(json.dumps({"mcp": kit.generate_mcp_entry("fake", kit.DEFAULT_MCP_URL), "skill_url": self.url.rsplit("/", 1)[0] + "/" + suffix}), encoding="utf8")
                result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "prepare", "codex", "-k", str(config)], cwd=self.project, capture_output=True, text=True, encoding="utf8")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.project / ".codex").exists())

    def test_intermediate_files_stay_in_repository_tmp_and_cleanup(self):
        self.prepare("claude")
        self.prepare("codex")
        archives = [Path(path) for path in kit.TMP_ZIPS]
        self.assertEqual(kit.TMP_ROOT, ROOT / ".tmp")
        self.assertEqual(len(archives), 2)
        for archive in archives:
            self.assertEqual(archive.parent, ROOT / ".tmp")
            self.assertTrue(archive.is_file())
        self.assertIsNone(kit.TMP_DIR)
        kit.cleanup()
        self.assertTrue(all(not archive.exists() for archive in archives))

    def test_literal_key_remains_compatible_and_uses_static_codex_headers(self):
        self.run_main("prepare", "codex", "-k", "literal-fixture-key")
        _, _, data = self.config("codex")
        self.assertEqual(data["mcp_servers"]["qpanda3-runtime"]["http_headers"], {"X-Api-Key": "literal-fixture-key"})

    def test_cli_invalid_client_config_does_not_echo_sensitive_contents(self):
        config = self.project / ".mcp.json"
        config.write_text('{"mcpServers": {"private": "secret-fixture-value"}, BROKEN}', encoding="utf8")
        result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "prepare", "claude", "-k", "fake"], cwd=self.project, capture_output=True, text=True, encoding="utf8")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("secret-fixture-value", result.stdout + result.stderr)

    def test_help_and_list_are_read_only(self):
        self.assertIn("project scope by default", self.run_main("help"))
        listing = self.run_main("list")
        self.assertIn("codebuddy", listing)
        self.assertEqual(list(self.project.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
