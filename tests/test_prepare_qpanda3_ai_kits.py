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
    extra_archives = {}

    def do_GET(self):
        if self.path == "/missing.zip":
            self.send_error(404)
            return
        path = self.path.split("?", 1)[0]
        content = self.extra_archives.get(path, skill_zip() if path == "/qpanda3-programming.zip" else b"invalid zip")
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
        # Keep the fixture independent of the developer's real client roots.
        self.overrides = patch.dict(os.environ, {name: "" for name in (
            "CODEX_HOME", "CLAUDE_CONFIG_DIR", "GEMINI_CLI_HOME", "XDG_CONFIG_HOME",
            "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT",
            "CLINE_DIR", "CLINE_DATA_DIR", "CLINE_MCP_SETTINGS_PATH", "CODEBUDDY_CONFIG_DIR",
            "CODEWHALE_HOME", "CODEWHALE_CONFIG_PATH", "DEEPSEEK_HOME", "DEEPSEEK_CONFIG_PATH",
        )})
        self.overrides.start()

    def tearDown(self):
        kit.cleanup()
        self.overrides.stop()
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

    def test_opencode_updates_existing_json_or_jsonc_and_uninstalls_all_active_files(self):
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
                self.assertNotIn("qpanda3-runtime", kit.load_client_config(json_file)["mcp"])
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

    def test_toml_serialization_failure_preserves_bytes_and_skill(self):
        self.prepare_literal("codex")
        config, skills = self.expected_paths("codex", "project")
        before = config.read_bytes()
        marker = skills / "qpanda3-programming/local.txt"
        marker.write_text("keep", encoding="utf8")
        bad = self.project / "input.json"
        entry = kit.generate_mcp_entry("fake", kit.DEFAULT_MCP_URL)
        entry["qpanda3-runtime"]["unsupported_null"] = None
        write_fixture(bad, {"mcp": entry})
        with self.assertRaises(TypeError):
            self.run_main("prepare", "codex", "-k", str(bad))
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(marker.read_text(encoding="utf8"), "keep")

    def test_atomic_replace_failure_retains_original_file_and_removes_temporary(self):
        path = self.project / "config.json"
        path.write_bytes(b'{"original": true}\r\n')
        before = path.read_bytes()
        with patch.object(kit.os, "replace", side_effect=OSError("fixture replace failure")), self.assertRaises(OSError):
            kit.write_client_config(path, {"new": True})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.project.iterdir()), [path])

    def test_multi_file_write_failure_restores_previous_config(self):
        paths = [self.project / "a.json", self.project / "b.json"]
        for path in paths:
            path.write_bytes(b'{"keep":true}\r\n')
        replace = kit.os.replace
        failed = False
        def fail_second(source, destination):
            nonlocal failed
            if destination == paths[1] and not failed:
                failed = True
                raise OSError("fixture second-file failure")
            return replace(source, destination)
        with patch.object(kit.os, "replace", side_effect=fail_second), self.assertRaises(OSError):
            kit.write_config_batch([(p, {"new": True}) for p in paths], self.project)
        self.assertTrue(all(p.read_bytes() == b'{"keep":true}\r\n' for p in paths))

    def test_jsonc_block_comments_and_comment_markers_inside_strings(self):
        config = self.project / "opencode.jsonc"
        config.write_text('/* setup */ {"url":"https://example.invalid/a/*b*/", "mcp":{}, /* block\ncomment */ "keep":42,}', encoding="utf8")
        self.prepare_literal("opencode")
        data = kit.load_client_config(config)
        self.assertEqual(data["url"], "https://example.invalid/a/*b*/")
        self.assertEqual(data["keep"], 42)

    def test_same_name_controls_are_preserved_for_every_client(self):
        controls = {
            "opencode": {"enabled": False, "timeout": 9876},
            "claude": {"customControl": "keep"},
            "codex": {"enabled": False, "enabled_tools": ["device_list"], "startup_timeout_sec": 42},
            "gemini": {"timeout": 9876, "includeTools": ["device_list"]},
            "codebuddy": {"disabled": True, "timeout": 9876},
            "cline": {"disabled": True, "autoApprove": ["device_list"], "timeout": 42},
            "codewhale": {"disabled": True, "enabled": False, "required": False},
            "zcode": {"enable": False, "timeout": 9876},
        }
        for tool, settings in controls.items():
            scope = "project" if tool in PROJECT_LAYOUTS else "user"
            with self.subTest(tool=tool):
                config, _ = self.expected_paths(tool, scope)
                data = config_fixture(tool)
                servers_for(tool, data)["qpanda3-runtime"] = {"url": "https://old.invalid", **settings}
                write_fixture(config, data)
                self.prepare_literal(tool, scope)
                actual = servers_for(tool, kit.load_client_config(config))["qpanda3-runtime"]
                for key, value in settings.items():
                    self.assertEqual(actual[key], value)

    def test_codex_credential_mode_changes_remove_old_auth_but_retain_custom_header(self):
        self.prepare_literal("codex")
        config, _, data = self.config("codex")
        data["mcp_servers"]["qpanda3-runtime"]["http_headers"]["X-Custom"] = "keep"
        kit.write_client_config(config, data)
        self.prepare("codex")
        entry = self.config("codex")[2]["mcp_servers"]["qpanda3-runtime"]
        self.assertEqual(entry["http_headers"], {"X-Custom": "keep"})
        self.assertEqual(entry["env_http_headers"], {"X-Api-Key": "QPANDA3_TEST_KEY"})
        self.prepare_literal("codex")
        entry = self.config("codex")[2]["mcp_servers"]["qpanda3-runtime"]
        self.assertNotIn("env_http_headers", entry)
        self.assertEqual(entry["http_headers"]["X-Custom"], "keep")

    def test_opencode_all_files_and_nested_containers_update_and_uninstall(self):
        paths = [self.project / name for name in ("opencode.jsonc", "opencode.json", ".opencode/opencode.jsonc", ".opencode/opencode.json")]
        for path in paths:
            write_fixture(path, {"mcp": {"qpanda3-runtime": {"type": "remote", "url": "https://old.invalid", "enabled": False}, "servers": {"qpanda3-runtime": {"url": "https://old.invalid", "enabled": False}, "other": {"command": ["keep"]}}}, "keep": 42})
        self.prepare_literal("opencode")
        for path in paths:
            data = kit.load_client_config(path)
            self.assertEqual(data["mcp"]["qpanda3-runtime"]["url"], kit.DEFAULT_MCP_URL)
            self.assertFalse(data["mcp"]["qpanda3-runtime"]["enabled"])
            self.assertEqual(data["mcp"]["servers"]["qpanda3-runtime"]["url"], kit.DEFAULT_MCP_URL)
        self.run_main("uninstall", "opencode")
        for path in paths:
            data = kit.load_client_config(path)
            self.assertNotIn("qpanda3-runtime", data["mcp"])
            self.assertNotIn("qpanda3-runtime", data["mcp"]["servers"])
            self.assertEqual(data["mcp"]["servers"]["other"], {"command": ["keep"]})

    def test_opencode_legacy_url_and_stdio_are_converted_and_unknown_entries_fail(self):
        config = self.project / "opencode.json"
        write_fixture(config, {"mcpServers": {"web": {"url": "https://example.invalid", "disabled": True}, "local": {"command": "node", "args": ["server.js"], "env": {"TEST": "fixture"}}}})
        self.prepare_literal("opencode")
        data = kit.load_client_config(config)["mcp"]
        self.assertEqual(data["web"], {"type": "remote", "url": "https://example.invalid", "enabled": False})
        self.assertEqual(data["local"], {"type": "local", "command": ["node", "server.js"], "environment": {"TEST": "fixture"}})
        write_fixture(config, {"mcpServers": {"unknown": {"strange": True}}})
        before = config.read_bytes()
        with self.assertRaises(ValueError):
            self.prepare_literal("opencode")
        self.assertEqual(config.read_bytes(), before)

    def test_zcode_imports_effective_fallback_without_losing_controls(self):
        fallback = self.project / ".agents/mcp.json"
        write_fixture(fallback, {"mcpServers": {"other": {"command": "keep", "enable": False}, "qpanda3-runtime": {"url": "https://old.invalid", "enable": False}}})
        before = fallback.read_bytes()
        self.prepare_literal("zcode")
        entry = self.config("zcode")[2]["mcp"]["servers"]
        self.assertEqual(entry["other"], {"command": "keep", "enable": False})
        self.assertFalse(entry["qpanda3-runtime"]["enable"])
        self.assertEqual(fallback.read_bytes(), before)
        self.run_main("uninstall", "zcode")
        self.assertNotIn("qpanda3-runtime", kit.load_client_config(fallback)["mcpServers"])
        self.assertIn("other", kit.load_client_config(fallback)["mcpServers"])

    def test_claude_creation_preserves_codebuddy_effective_fallback(self):
        fallback = self.project / "mcp.json"
        write_fixture(fallback, {"mcpServers": {"other": {"command": "keep"}}})
        before = fallback.read_bytes()
        self.prepare_literal("claude")
        self.assertEqual(self.config("claude")[2]["mcpServers"]["other"], {"command": "keep"})
        self.assertEqual(fallback.read_bytes(), before)

    def test_codewhale_uses_legacy_state_without_shadowing_it(self):
        legacy = self.home / ".deepseek/mcp.json"
        write_fixture(legacy, {"servers": {"other": {"command": "keep"}}})
        self.prepare_literal("codewhale", "user")
        self.assertFalse((self.home / ".codewhale/mcp.json").exists())
        self.assertIn("other", kit.load_client_config(legacy)["servers"])
        self.run_main("uninstall", "codewhale", "--user")
        self.assertEqual(kit.load_client_config(legacy), {"servers": {"other": {"command": "keep"}}})

    def test_skill_update_removes_obsolete_files(self):
        self.prepare_literal("claude")
        target = self.project / ".claude/skills/qpanda3-programming"
        (target / "obsolete.txt").write_text("old", encoding="utf8")
        self.prepare_literal("claude")
        self.assertFalse((target / "obsolete.txt").exists())
        self.assertTrue((target / "resources/example.txt").is_file())

    def test_bad_new_skill_layout_does_not_use_old_skill_md_or_change_config(self):
        self.prepare_literal("claude")
        target = self.project / ".claude/skills/qpanda3-programming"
        config = self.project / ".mcp.json"
        before = config.read_bytes()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("outer/inner/SKILL.md", "wrong layout")
        with patch.dict(FixtureHandler.extra_archives, {"/qpanda3-programming.zip": output.getvalue()}), self.assertRaises(ValueError):
            self.prepare_literal("claude")
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual((target / "resources/example.txt").read_text(encoding="utf8"), "fixture reference")

    def test_skill_copy_and_config_failures_restore_old_tree(self):
        self.prepare_literal("claude")
        target = self.project / ".claude/skills/qpanda3-programming"
        config = self.project / ".mcp.json"
        (target / "old.txt").write_text("keep", encoding="utf8")
        before = config.read_bytes()
        copytree = kit.shutil.copytree
        failed = False
        def fail_install(source, destination, *args, **kwargs):
            nonlocal failed
            if Path(destination) == target and not failed:
                failed = True
                target.mkdir(parents=True)
                (target / "partial.txt").write_text("partial", encoding="utf8")
                raise OSError("fixture copy failure")
            return copytree(source, destination, *args, **kwargs)
        with patch.object(kit.shutil, "copytree", side_effect=fail_install), self.assertRaises(OSError):
            self.prepare_literal("claude")
        self.assertEqual((target / "old.txt").read_text(encoding="utf8"), "keep")
        self.assertFalse((target / "partial.txt").exists())
        with patch.object(kit, "write_config_batch", side_effect=OSError("fixture config failure")), self.assertRaises(OSError):
            self.prepare_literal("claude")
        self.assertEqual((target / "old.txt").read_text(encoding="utf8"), "keep")
        self.assertEqual(config.read_bytes(), before)

    def make_directory_link(self, link, destination):
        try:
            link.symlink_to(destination, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(destination)], capture_output=True)
            self.assertEqual(result.returncode, 0, "Cannot create junction fixture")

    def test_linked_config_and_skill_parents_cannot_escape_scope(self):
        for tool in ("codex", "gemini", "zcode"):
            with self.subTest(tool=tool):
                config, _ = self.expected_paths(tool, "project")
                outside = self.base / (tool + "-outside")
                outside.mkdir()
                path = outside / config.name
                write_fixture(path, config_fixture(tool))
                before = path.read_bytes()
                self.make_directory_link(config.parent, outside)
                with self.assertRaises(ValueError):
                    self.prepare_literal(tool)
                with self.assertRaises(ValueError):
                    self.run_main("uninstall", tool)
                self.assertEqual(path.read_bytes(), before)
                if config.parent.is_symlink():
                    config.parent.unlink()
                else:
                    config.parent.rmdir()  # Remove the fixture junction itself only.
        outside = self.base / "outside-skills"
        outside.mkdir()
        (self.project / ".claude").mkdir()
        self.make_directory_link(self.project / ".claude/skills", outside)
        with self.assertRaises(ValueError):
            self.prepare_literal("claude")
        self.assertEqual(list(outside.iterdir()), [])

    def test_custom_root_overrides_fail_before_default_paths_are_written(self):
        cases = {"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR", "gemini": "GEMINI_CLI_HOME", "opencode": "OPENCODE_CONFIG_DIR", "cline": "CLINE_MCP_SETTINGS_PATH", "codewhale": "CODEWHALE_HOME"}
        for tool, name in cases.items():
            scope = "user"
            with self.subTest(tool=tool), patch.dict(os.environ, {name: str(self.base / "custom")}):
                with self.assertRaises(ValueError):
                    self.prepare_literal(tool, scope)
        self.assertEqual(list(self.project.iterdir()), [])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_project_scope_ignores_user_root_overrides(self):
        cases = {"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR", "opencode": "XDG_CONFIG_HOME"}
        for tool, name in cases.items():
            with self.subTest(tool=tool), patch.dict(os.environ, {name: str(self.base / "custom")}):
                self.prepare_literal(tool)
                config, skills = self.expected_paths(tool, "project")
                self.assertTrue(config.is_file())
                self.assertTrue((skills / "qpanda3-programming/SKILL.md").is_file())
                with patch.object(kit.shutil, "which", return_value="fixture-client"):
                    self.assertTrue(kit.check_tool_usable(tool, "project"))
                self.run_main("uninstall", tool)
                self.assertNotIn("qpanda3-runtime", servers_for(tool, kit.load_client_config(config)))
        self.assertFalse((self.base / "custom").exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_project_scope_still_rejects_opencode_injected_config(self):
        for name in ("OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG_CONTENT"):
            with self.subTest(name=name), patch.dict(os.environ, {name: "custom"}):
                with self.assertRaises(ValueError):
                    self.prepare_literal("opencode")

    def test_user_scope_accepts_linked_home_and_linked_home_parent(self):
        link = self.base / "home-link"
        self.make_directory_link(link, self.home)
        nested = self.home / "nested"
        nested.mkdir()
        for home, actual in ((link, self.home), (link / "nested", nested)):
            with self.subTest(home=home), patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}):
                for tool in USER_LAYOUTS:
                    self.prepare_literal(tool, "user")
                    config, skills = USER_LAYOUTS[tool]
                    self.assertTrue((actual / config).is_file())
                    self.assertTrue((actual / skills / "qpanda3-programming/SKILL.md").is_file())
                    self.run_main("uninstall", tool, "--user")
                    self.assertNotIn("qpanda3-runtime", servers_for(tool, kit.load_client_config(actual / config)))

    def test_large_standard_json_uses_fast_parser_without_losing_data(self):
        config = self.home / ".claude.json"
        data = {"history": ["x" * 100 for _ in range(10000)], "nested": {"keep": True}}
        write_fixture(config, data)
        # This boundary check avoids a machine-dependent timing threshold.
        with patch.object(kit.json5, "loads", side_effect=AssertionError("JSON5 slow path")):
            self.assertEqual(kit.load_client_config(config), data)

    def test_linked_home_does_not_allow_linked_config_below_root(self):
        link = self.base / "home-link"
        outside = self.base / "outside"
        outside.mkdir()
        self.make_directory_link(link, self.home)
        self.make_directory_link(self.home / ".codex", outside)
        config = outside / "config.toml"
        write_fixture(config, config_fixture("codex"))
        before = config.read_bytes()
        with patch.dict(os.environ, {"HOME": str(link), "USERPROFILE": str(link)}):
            with self.assertRaises(ValueError):
                self.prepare_literal("codex", "user")
            with self.assertRaises(ValueError):
                self.run_main("uninstall", "codex", "--user")
        self.assertEqual(config.read_bytes(), before)

    def test_json_and_json5_duplicate_keys_remain_rejected(self):
        config = self.project / "config.json"
        for source in ('{"nested":{"secret":1,"secret":2}}', '{/* comment */ nested: {secret: 1, secret: 2}}'):
            with self.subTest(source=source):
                config.write_text(source, encoding="utf8")
                with self.assertRaisesRegex(ValueError, "^Invalid client JSON configuration$"):
                    kit.load_client_config(config)

    def test_codebuddy_lowercase_environment_name_is_rejected(self):
        with patch.dict(os.environ, {"review_lowercase": "fixture"}), self.assertRaises(SystemExit):
            self.run_main("prepare", "codebuddy", "--api-key-env", "review_lowercase")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_signed_skill_url_does_not_expose_query_fragment_or_userinfo(self):
        logs = self.prepare_literal("claude", skill_url=self.url + "?token=fixture-signed-secret#private-fragment")
        self.assertNotIn("fixture-signed-secret", logs)
        self.assertNotIn("private-fragment", logs)
        output = io.StringIO()
        with patch.object(kit.urllib.request, "urlopen", side_effect=ValueError("bad url")), contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            kit.shared_download_zip("https://private-user:private-password@example.invalid/skill.zip?token=private-token")
        self.assertNotIn("private-user", output.getvalue())
        self.assertNotIn("private-password", output.getvalue())
        self.assertNotIn("private-token", output.getvalue())

    def test_shared_mcp_can_be_retained_with_skill_only_uninstall(self):
        self.prepare_literal("claude")
        self.prepare_literal("codebuddy")
        config = self.project / ".mcp.json"
        before = config.read_bytes()
        self.run_main("uninstall", "claude", "--skill-only")
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((self.project / ".claude/skills/qpanda3-programming").exists())
        self.assertTrue((self.project / ".codebuddy/skills/qpanda3-programming/SKILL.md").is_file())

    def test_opencode_conflicting_container_controls_are_not_silently_chosen(self):
        config = self.project / "opencode.jsonc"
        write_fixture(config, {"mcp": {"qpanda3-runtime": {"url": "https://old.invalid", "enabled": False}, "servers": {"qpanda3-runtime": {"url": "https://old.invalid", "enabled": True}}}})
        before = config.read_bytes()
        with self.assertRaises(ValueError):
            self.prepare_literal("opencode")
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((self.project / ".opencode/skills").exists())

    def test_opencode_nested_mapping_may_contain_servers_named_type_and_enabled(self):
        config = self.project / "opencode.jsonc"
        other = {"type": {"type": "remote", "url": "https://example.invalid/type"}, "enabled": {"type": "local", "command": ["keep"]}}
        write_fixture(config, {"mcp": {"servers": other}})
        self.prepare_literal("opencode")
        actual = kit.load_client_config(config)
        self.assertIn("qpanda3-runtime", actual["mcp"]["servers"])
        self.run_main("uninstall", "opencode")
        self.assertEqual(kit.load_client_config(config), {"mcp": {"servers": other}})

    def test_codebuddy_json_input_rejects_lowercase_header_variable(self):
        config = self.project / "input.json"
        write_fixture(config, {"mcp": kit.generate_mcp_entry("${review_lowercase}", kit.DEFAULT_MCP_URL)})
        with self.assertRaises(ValueError):
            self.run_main("prepare", "codebuddy", "-k", str(config))
        self.assertFalse((self.project / ".mcp.json").exists())

    def test_codewhale_custom_mcp_config_path_requires_manual_configuration(self):
        config = self.home / ".codewhale/config.toml"
        write_fixture(config, {"mcp_config_path": str(self.base / "custom-mcp.json")})
        before = config.read_bytes()
        with self.assertRaises(ValueError):
            self.prepare_literal("codewhale", "user")
        self.assertEqual(config.read_bytes(), before)
        self.assertFalse(config.with_name("mcp.json").exists())

    def test_uninstall_aborts_before_any_write_if_claude_approval_is_linked(self):
        self.prepare_literal("claude")
        config = self.project / ".mcp.json"
        before = config.read_bytes()
        outside = self.base / "outside-approvals"
        outside.mkdir()
        write_fixture(outside / "settings.local.json", {"enabledMcpjsonServers": ["qpanda3-runtime"]})
        settings = self.project / ".claude/settings.local.json"
        # Use a directory link since Windows can create junctions without symlink privileges.
        other_claude = self.project / ".claude-original"
        settings.parent.rename(other_claude)
        self.make_directory_link(settings.parent, outside)
        with self.assertRaises(ValueError):
            self.run_main("uninstall", "claude")
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(kit.load_client_config(outside / "settings.local.json"), {"enabledMcpjsonServers": ["qpanda3-runtime"]})

    def test_zip_paths_links_and_extraction_limits_are_rejected(self):
        unsafe = ("../SKILL.md", "/SKILL.md", "C:/SKILL.md", "wrapper\\SKILL.md")
        for index, name in enumerate(unsafe):
            with self.subTest(path=name):
                path = self.base / f"unsafe-{index}.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    info = zipfile.ZipInfo("placeholder")
                    # ZipInfo's constructor normalizes Windows separators;
                    # assign the raw member name to model an externally built ZIP.
                    info.filename = info.orig_filename = name
                    archive.writestr(info, "fixture")
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                    kit.shared_validate_zip(path)
        path = self.base / "linked.zip"
        with zipfile.ZipFile(path, "w") as archive:
            info = zipfile.ZipInfo("SKILL.md")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "elsewhere")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            kit.shared_validate_zip(path)
        path = self.base / "too-many.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("SKILL.md", "fixture")
            for index in range(10000):
                archive.writestr(f"files/{index}", "")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            kit.shared_validate_zip(path)

    def test_config_edited_during_download_is_not_overwritten(self):
        self.prepare_literal("claude")
        config = self.project / ".mcp.json"
        target = self.project / ".claude/skills/qpanda3-programming"
        (target / "local.txt").write_text("keep", encoding="utf8")
        download = kit.shared_download_zip
        recent = {"mcpServers": {"new-personal-server": {"command": "keep"}}}
        def edit_during_download(url):
            name = download(url)
            write_fixture(config, recent)
            return name
        with patch.object(kit, "shared_download_zip", side_effect=edit_during_download), self.assertRaises(ValueError):
            self.prepare_literal("claude")
        self.assertEqual(kit.load_client_config(config), recent)
        self.assertEqual((target / "local.txt").read_text(encoding="utf8"), "keep")


if __name__ == "__main__":
    unittest.main()
