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

PROJECT_LAYOUTS = {
    "opencode": ("opencode.jsonc", ".opencode/skills"),
    "claude": (".mcp.json", ".claude/skills"),
    "codex": (".codex/config.toml", ".agents/skills"),
    "gemini": (".gemini/settings.json", ".gemini/skills"),
    "codebuddy": (".mcp.json", ".codebuddy/skills"),
    "zcode": (".zcode/config.json", ".zcode/skills"),
}
USER_LAYOUTS = {
    "opencode": (".config/opencode/opencode.jsonc", ".config/opencode/skills"),
    "claude": (".claude.json", ".claude/skills"),
    "codex": (".codex/config.toml", ".agents/skills"),
    "gemini": (".gemini/settings.json", ".gemini/skills"),
    "codebuddy": (".codebuddy/.mcp.json", ".codebuddy/skills"),
    "cline": (".cline/data/settings/cline_mcp_settings.json", ".cline/skills"),
    "codewhale": (".codewhale/mcp.json", ".codewhale/skills"),
    "zcode": (".zcode/cli/config.json", ".zcode/skills"),
}


def servers_for(tool, data):
    if tool == "zcode":
        return data["mcp"]["servers"]
    key = {"opencode": "mcp", "codex": "mcp_servers", "codewhale": "servers"}.get(tool, "mcpServers")
    return data[key]


def config_fixture(tool):
    other = {"url": "https://example.invalid/other", "enabled": False}
    if tool == "zcode":
        return {"keep": 42, "mcp": {"custom": "keep", "servers": {"other": other}}}
    key = {"opencode": "mcp", "codex": "mcp_servers", "codewhale": "servers"}.get(tool, "mcpServers")
    return {"keep": 42, key: {"other": other}}


def write_fixture(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = kit.tomli_w.dumps(data) if path.suffix == ".toml" else json.dumps(data)
    path.write_text(text, encoding="utf8")


def skill_zip():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("qpanda3-programming/SKILL.md", "---\nname: qpanda3-programming\ndescription: A local test skill fixture.\n---\nTest fixture\n")
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

    def run_main(self, *args, cwd=None, skill_url=None):
        output = io.StringIO()
        previous = Path.cwd()
        try:
            os.chdir(cwd or self.project)
            with patch.object(sys, "argv", [str(SCRIPT), *args]), patch.object(kit, "DEFAULT_SKILL_URL", skill_url or self.url), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                kit.main()
        finally:
            os.chdir(previous)
        return output.getvalue()

    def prepare(self, tool, *args):
        return self.run_main("prepare", tool, "--api-key-env", "QPANDA3_TEST_KEY", *args)

    def prepare_literal(self, tool, scope="project", **kwargs):
        scope_args = ("--user",) if scope == "user" else ()
        return self.run_main("prepare", tool, "-k", "literal-fixture-key", *scope_args, **kwargs)

    def expected_paths(self, tool, scope):
        root = self.project if scope == "project" else self.home
        layout = PROJECT_LAYOUTS if scope == "project" else USER_LAYOUTS
        config, skills = layout[tool]
        return root / config, root / skills

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
        for tool in PROJECT_LAYOUTS:
            with self.subTest(tool=tool):
                self.prepare_literal(tool)
                project_config, skills, _ = self.config(tool)
                before = project_config.read_bytes()
                self.prepare_literal(tool, "user")
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
        for args in (("prepare", "typo", "-k", "fake"), ("uninstall",), ("uninstall", "codex", "-p", "--user"), ("prepare", "cline", "-k", "fake"), ("prepare", "codewhale", "-k", "fake")):
            with self.subTest(args=args), self.assertRaises(SystemExit) as result:
                self.run_main(*args)
            self.assertNotEqual(result.exception.code, 0)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_missing_environment_variable_writes_nothing(self):
        with patch.dict(os.environ, {"QPANDA3_TEST_KEY": ""}), self.assertRaises(SystemExit):
            self.prepare("codex")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_broken_existing_config_is_not_overwritten(self):
        for tool in USER_LAYOUTS:
            scope = "project" if tool in PROJECT_LAYOUTS else "user"
            with self.subTest(tool=tool):
                config, _ = self.expected_paths(tool, scope)
                config.parent.mkdir(parents=True, exist_ok=True)
                config.write_text("{ invalid", encoding="utf8")
                with self.assertRaises(ValueError):
                    self.prepare_literal(tool, scope)
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
        self.assertEqual(set(kit.SUPPORTED_TOOLS), set(USER_LAYOUTS))
        for tool in USER_LAYOUTS:
            self.assertIn(tool, listing)
        self.assertIn("user only (--user)", listing)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_all_eight_clients_write_expected_paths_and_http_formats(self):
        headers = {"X-Api-Key": "literal-fixture-key"}
        basic = {"url": kit.DEFAULT_MCP_URL, "headers": headers}
        expected = {
            "opencode": {**basic, "type": "remote"},
            "claude": {**basic, "type": "http"},
            "codex": {"url": kit.DEFAULT_MCP_URL, "http_headers": headers},
            "gemini": {"httpUrl": kit.DEFAULT_MCP_URL, "headers": headers},
            "codebuddy": {**basic, "type": "http"},
            "cline": {**basic, "type": "streamableHttp", "disabled": False, "autoApprove": []},
            "codewhale": {**basic, "enabled": True},
            "zcode": {**basic, "type": "http"},
        }
        for tool in USER_LAYOUTS:
            scope = "project" if tool in PROJECT_LAYOUTS else "user"
            with self.subTest(tool=tool, scope=scope):
                logs = self.prepare_literal(tool, scope)
                config, skills = self.expected_paths(tool, scope)
                data = kit.load_client_config(config)
                self.assertEqual(servers_for(tool, data)["qpanda3-runtime"], expected[tool])
                self.assertTrue((skills / "qpanda3-programming/SKILL.md").is_file())
                self.assertNotIn("literal-fixture-key", logs)
        self.assertFalse((self.project / ".opencode/opencode.jsonc").exists())
        self.assertFalse((self.home / ".codewhale/constitution.json").exists())

    def test_all_clients_repeat_merge_and_uninstall_preserve_other_values(self):
        for tool in USER_LAYOUTS:
            scopes = ("project", "user") if tool in PROJECT_LAYOUTS else ("user",)
            for scope in scopes:
                with self.subTest(tool=tool, scope=scope):
                    config, skills = self.expected_paths(tool, scope)
                    original = config_fixture(tool)
                    write_fixture(config, original)
                    self.prepare_literal(tool, scope)
                    first = config.read_bytes()
                    self.prepare_literal(tool, scope)
                    self.assertEqual(config.read_bytes(), first)
                    data = kit.load_client_config(config)
                    self.assertEqual(data["keep"], 42)
                    self.assertEqual(servers_for(tool, data)["other"], servers_for(tool, original)["other"])
                    if tool == "zcode":
                        self.assertEqual(data["mcp"]["custom"], "keep")
                    other_skill = skills / "other-skill/keep.txt"
                    other_skill.parent.mkdir(parents=True, exist_ok=True)
                    other_skill.write_text("keep", encoding="utf8")
                    args = ("--user",) if scope == "user" else ()
                    self.run_main("uninstall", tool, *args)
                    self.assertEqual(kit.load_client_config(config), original)
                    self.assertFalse((skills / "qpanda3-programming").exists())
                    self.assertEqual(other_skill.read_text(encoding="utf8"), "keep")

    def test_all_clients_http_failure_and_invalid_zip_preserve_existing_config(self):
        for tool in USER_LAYOUTS:
            scope = "project" if tool in PROJECT_LAYOUTS else "user"
            for suffix in ("missing.zip", "invalid.zip"):
                with self.subTest(tool=tool, suffix=suffix):
                    config, skills = self.expected_paths(tool, scope)
                    write_fixture(config, config_fixture(tool))
                    before = config.read_bytes()
                    with self.assertRaises(SystemExit):
                        self.prepare_literal(tool, scope, skill_url=self.url.rsplit("/", 1)[0] + "/" + suffix)
                    self.assertEqual(config.read_bytes(), before)
                    self.assertFalse((skills / "qpanda3-programming").exists())

    def test_invalid_server_containers_are_not_overwritten(self):
        for tool in USER_LAYOUTS:
            scope = "project" if tool in PROJECT_LAYOUTS else "user"
            with self.subTest(tool=tool):
                config, skills = self.expected_paths(tool, scope)
                data = config_fixture(tool)
                if tool == "zcode":
                    data["mcp"]["servers"] = []
                else:
                    data[next(key for key in data if key != "keep")] = []
                write_fixture(config, data)
                before = config.read_bytes()
                with self.assertRaises(ValueError):
                    self.prepare_literal(tool, scope)
                self.assertEqual(config.read_bytes(), before)
                self.assertFalse((skills / "qpanda3-programming").exists())

    def test_opencode_updates_existing_json_or_jsonc_and_keeps_history(self):
        for scope in ("project", "user"):
            with self.subTest(scope=scope):
                default, skills = self.expected_paths("opencode", scope)
                json_file = default.with_suffix(".json")
                write_fixture(json_file, config_fixture("opencode"))
                historical = self.project / ".opencode/opencode.jsonc"
                historical.parent.mkdir(exist_ok=True)
                historical.write_text('{"historical":true}', encoding="utf8")
                self.prepare_literal("opencode", scope)
                self.assertFalse(default.exists())
                self.assertIn("qpanda3-runtime", json.loads(json_file.read_text(encoding="utf8"))["mcp"])
                self.assertEqual(historical.read_text(encoding="utf8"), '{"historical":true}')
                default.write_text('// keep value, comments may be reformatted\n{"custom":7,"mcp":{}}', encoding="utf8")
                before_json = json_file.read_bytes()
                self.prepare_literal("opencode", scope)
                self.assertEqual(json_file.read_bytes(), before_json)
                self.assertEqual(json.loads(default.read_text(encoding="utf8"))["custom"], 7)
                args = ("--user",) if scope == "user" else ()
                self.run_main("uninstall", "opencode", *args)
                self.assertNotIn("qpanda3-runtime", json.loads(default.read_text(encoding="utf8"))["mcp"])
                self.assertEqual(json_file.read_bytes(), before_json)
                self.assertFalse((skills / "qpanda3-programming").exists())

    def test_codewhale_preserves_constitution_and_compatible_mcpservers_format(self):
        config, _ = self.expected_paths("codewhale", "user")
        constitution = config.parent / "constitution.json"
        constitution.parent.mkdir(parents=True)
        constitution.write_text('{"priorities":["my preference"]}', encoding="utf8")
        before = constitution.read_bytes()
        config.write_text('{"mcpServers":{"other":{"url":"https://example.invalid"}},"keep":42}', encoding="utf8")
        original = json.loads(config.read_text(encoding="utf8"))
        self.prepare_literal("codewhale", "user")
        data = json.loads(config.read_text(encoding="utf8"))
        self.assertNotIn("servers", data)
        self.assertIn("qpanda3-runtime", data["mcpServers"])
        self.run_main("uninstall", "codewhale", "--user")
        self.assertEqual(json.loads(config.read_text(encoding="utf8")), original)
        self.assertEqual(constitution.read_bytes(), before)

    def test_opencode_preserves_legacy_server_entries_when_normalizing(self):
        config, _ = self.expected_paths("opencode", "project")
        data = config_fixture("opencode")
        data["mcpServers"] = {
            "legacy": {"type": "remote", "url": "https://example.invalid/legacy"},
            "other": {"url": "https://example.invalid/outdated"},
        }
        write_fixture(config, data)
        self.prepare_literal("opencode")
        updated = json.loads(config.read_text(encoding="utf8"))
        self.assertNotIn("mcpServers", updated)
        self.assertEqual(updated["mcp"]["legacy"], data["mcpServers"]["legacy"])
        self.assertEqual(updated["mcp"]["other"], data["mcp"]["other"])
        self.assertEqual(updated["keep"], 42)


if __name__ == "__main__":
    unittest.main()
