#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyqpanda3",
#     "commentjson>=0.9.0",
#     "tomli-w>=1.2.0",
# ]
# ///
"""
prepare_qpanda3_ai_kits.py — Prepare QPanda3 dev environment for AI coding tools

Upstream: https://show.originqc.com.cn/prod/qcloudai/qpanda3-skill_MCP/prepare_qpanda3_ai_kits.py
Local changes: project scope by default, explicit project directory,
environment-backed credentials and scope-limited uninstall.
"""

import atexit
import json as stdjson
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import commentjson
import tomli_w
import tomllib

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.getcwd()
TMP_ROOT = Path(SCRIPT_DIR).parent / ".tmp"
TMP_ZIP = None
TMP_DIR = None
TMP_ZIPS = []


def cleanup():
    for path in TMP_ZIPS[:]:
        try:
            if os.path.isfile(path):
                os.unlink(path)
            TMP_ZIPS.remove(path)
        except OSError:
            log_warn(f"Temporary ZIP could not be removed: {path}")
    if TMP_DIR and os.path.isdir(TMP_DIR):
        shutil.rmtree(TMP_DIR, ignore_errors=True)


atexit.register(cleanup)


def log_info(msg):
    print(f"{BLUE}[INFO]{NC} {msg}")


def log_ok(msg):
    print(f"{GREEN}[OK]{NC} {msg}")


def log_warn(msg):
    print(f"{YELLOW}[WARN]{NC} {msg}")


def log_error(msg):
    print(f"{RED}[ERROR]{NC} {msg}")


# ══════════════════════ Default Configuration ══════════════════════
DEFAULT_MCP_URL = "https://qcloud.originqc.com.cn/qpanda/runtime/mcp"
DEFAULT_SKILL_URL = "https://show.originqc.com.cn/prod/qcloudai/qpanda3-skill_MCP/qpanda3-programming.zip"


def resolve_k_param(k_value: str):
    """
    Resolve -k parameter:
    - Resolve config paths from the working directory, then the script directory.
    - Otherwise treat as API key string, use global defaults
    Returns (mcp_cfg: dict | None, skill_url: str)
    """
    for filepath in (os.path.abspath(k_value), os.path.join(SCRIPT_DIR, k_value)):
        if os.path.isfile(filepath):
            with open(filepath, encoding="utf-8-sig") as f:
                data = stdjson.load(f)
            if not isinstance(data, dict):
                raise ValueError("MCP input must be a JSON object")
            mcp_cfg = data.get("mcp")
            validate_mcp_entries(mcp_cfg)
            skill_url = data.get("skill_url", DEFAULT_SKILL_URL)
            if not isinstance(skill_url, str) or not skill_url:
                raise ValueError("skill_url must be a non-empty string")
            log_info(f"Read config from: {filepath}")
            return mcp_cfg, skill_url
    if k_value.lower().endswith(".json") or os.path.sep in k_value:
        raise ValueError("Config file not found")
    return None, DEFAULT_SKILL_URL


def generate_mcp_entry(apikey: str, mcp_url: str) -> dict:
    """Generate a generic MCP server entry (same format as config file mcp field)"""
    return {"qpanda3-runtime": {"url": mcp_url, "headers": {"X-Api-Key": apikey}}}


def wrap_mcp_entry(tool: str, entry: dict) -> dict:
    """Wrap a generic MCP server entry into each tool's MCP JSON format"""
    if tool == "opencode":
        servers = {}
        for name, cfg in entry.items():
            servers[name] = dict(cfg)
            servers[name].setdefault("type", "remote")
        return {"mcp": servers}
    elif tool in ("claude", "codebuddy"):
        servers = {}
        for name, cfg in entry.items():
            servers[name] = dict(cfg)
            servers[name].setdefault("type", "http")
        return {"mcpServers": servers}
    elif tool == "codex":
        servers = {}
        for name, cfg in entry.items():
            cfg = dict(cfg)
            headers = cfg.pop("headers", {})
            static_headers = dict(cfg.get("http_headers", {}))
            env_headers = dict(cfg.get("env_http_headers", {}))
            for header, value in headers.items():
                match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
                if match:
                    env_headers[header] = match.group(1)
                else:
                    static_headers[header] = value
            if static_headers:
                cfg["http_headers"] = static_headers
            if env_headers:
                cfg["env_http_headers"] = env_headers
            for field in ("type", "transport"):
                cfg.pop(field, None)
            servers[name] = cfg
        return {"mcp_servers": servers}
    elif tool == "gemini":
        servers = {}
        for name, cfg in entry.items():
            cfg = dict(cfg)
            cfg["httpUrl"] = cfg.pop("url")
            for field in ("type", "transport"):
                cfg.pop(field, None)
            servers[name] = cfg
        return {"mcpServers": servers}
    elif tool == "cline":
        servers = {}
        for name, cfg in entry.items():
            servers[name] = {
                "disabled": False,
                "autoApprove": [],
                "type": "streamableHttp",
                "url": cfg["url"],
                "headers": cfg.get("headers", {}),
            }
        return {"mcpServers": servers}
    elif tool == "codewhale":
        servers = {}
        for name, cfg in entry.items():
            servers[name] = {
                "url": cfg["url"],
                "headers": cfg.get("headers", {}),
                "enabled": True,
            }
        return {"servers": servers}
    elif tool == "zcode":
        servers = {}
        for name, cfg in entry.items():
            servers[name] = {
                "type": "http",
                "url": cfg["url"],
                "headers": cfg.get("headers", {}),
            }
        return {"mcp": {"servers": servers}}
    return {}


USER_CONFIG_DIRS = {
    "opencode": ["~/.config/opencode"],
    "claude": ["~/.claude", "~/.claude.json"],
    "codex": ["~/.codex"],
    "gemini": ["~/.gemini"],
    "codebuddy": ["~/.codebuddy"],
    "cline": ["~/.cline"],
    "codewhale": ["~/.codewhale"],
    "zcode": ["~/.zcode"],
}

TOOL_COMMANDS = {
    "opencode": "opencode",
    "claude": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "codebuddy": "codebuddy",
    "cline": "cline",
    "zcode": "zcode",
}


def check_tool_usable(tool: str, scope: str) -> bool:
    if tool == "cline" and scope == "project":
        log_error(
            "Cline CLI MCP uses user-level configuration. Use 'prepare cline -k KEY --user'."
        )
        return False
    if tool == "codewhale" and scope == "project":
        log_error(
            "Codewhale MCP uses user-level configuration. Use 'prepare codewhale -k KEY --user'."
        )
        return False
    if scope == "project":
        cmd = TOOL_COMMANDS.get(tool)
        if cmd and shutil.which(cmd):
            return True
        log_warn(
            f"{SUPPORTED_TOOLS[tool]['name']} command not found in PATH ({cmd}), skipped"
        )
        return False
    paths = USER_CONFIG_DIRS.get(tool, [])
    for p in paths:
        if os.path.exists(os.path.expanduser(p)):
            return True
    log_warn(
        f"{SUPPORTED_TOOLS[tool]['name']} not detected ({', '.join(USER_CONFIG_DIRS.get(tool, []))}), skipped"
    )
    return False


def detect_installed_tools(scope: str = "user") -> list[str]:
    return [t for t in SUPPORTED_TOOLS if check_tool_usable(t, scope)]


SUPPORTED_TOOLS = {
    "opencode": {"name": "OpenCode"},
    "claude": {"name": "Claude Code"},
    "codex": {"name": "Codex"},
    "gemini": {"name": "Gemini CLI"},
    "codebuddy": {"name": "CodeBuddy"},
    "cline": {"name": "Cline"},
    "codewhale": {"name": "Codewhale"},
    "zcode": {"name": "ZCode Agent"},
}


# ══════════════════════ Step 1: pyqpanda3 ══════════════════════


def run_step1():
    log_info("Step 1: Verify pyqpanda3")
    try:
        import pyqpanda3

        log_ok("pyqpanda3 ready")
    except ImportError:
        log_error("pyqpanda3 unavailable")


# ══════════════════════ Shared Helper Functions ══════════════════════


def shared_download_zip(url):
    global TMP_ZIP, SKILL_NAME, SKILL_ZIP_PATH
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(prefix="_skill.", suffix=".zip", dir=TMP_ROOT, delete=False)
    TMP_ZIP = tmp.name
    TMP_ZIPS.append(TMP_ZIP)
    tmp.close()
    log_info(f"Downloading: {url}")
    try:
        with urllib.request.urlopen(url, timeout=30) as response, open(TMP_ZIP, "wb") as output:
            shutil.copyfileobj(response, output)
    except Exception as e:
        if isinstance(e, urllib.error.HTTPError):
            e.close()
        log_error(f"Download failed ({type(e).__name__}); check skill_url and network")
        sys.exit(1)
    log_ok("Download complete")
    shared_validate_zip(TMP_ZIP)
    SKILL_NAME = os.path.splitext(os.path.basename(urlparse(url).path))[0]
    SKILL_ZIP_PATH = TMP_ZIP
    return SKILL_NAME


def shared_use_local_zip(path):
    global SKILL_NAME
    shared_validate_zip(path)
    SKILL_NAME = os.path.splitext(os.path.basename(path))[0]
    return path


def shared_validate_zip(zp):
    try:
        with zipfile.ZipFile(zp) as z:
            r = z.testzip()
            if r:
                log_error(f"Corrupt ZIP: {r}")
                sys.exit(1)
            for name in z.namelist():
                parts = Path(name.replace("\\", "/")).parts
                if ".." in parts or name.startswith(("/", "\\")) or ":" in name:
                    raise ValueError("Unsafe archive path")
            if not any(name == "SKILL.md" or name.endswith("/SKILL.md") for name in z.namelist()):
                raise ValueError("Skill archive contains no SKILL.md")
    except Exception as e:
        log_error(f"Invalid ZIP: {e}")
        sys.exit(1)
    s = os.path.getsize(zp)
    log_ok(f"Valid ZIP ({s // 1024}K)" if s >= 1024 else f"Valid ZIP ({s}B)")


def shared_extract_and_install(zip_path, target, desc):
    global TMP_DIR
    os.makedirs(target, exist_ok=True)
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    td = tempfile.mkdtemp(prefix="_skill_extract.", dir=TMP_ROOT)
    TMP_DIR = td
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(td)
        _items = os.listdir(td)
        if len(_items) == 1 and os.path.isdir(os.path.join(td, _items[0])):
            _inner = os.path.join(td, _items[0])
            for _item in os.listdir(_inner):
                shutil.move(os.path.join(_inner, _item), os.path.join(td, _item))
            os.rmdir(_inner)
        if shutil.which("rsync"):
            subprocess.run(
                [
                    "rsync",
                    "-a",
                    "--exclude=build_index.py",
                    "--exclude=verify_sync.py",
                    f"{td}/",
                    f"{target}/",
                ],
                check=True,
            )
        else:
            for item in os.listdir(td):
                s = os.path.join(td, item)
                d = os.path.join(target, item)
                if item in ("build_index.py", "verify_sync.py"):
                    continue
                if os.path.isdir(s):
                    shutil.copytree(
                        s,
                        d,
                        ignore=shutil.ignore_patterns(
                            "build_index.py", "verify_sync.py"
                        ),
                        dirs_exist_ok=True,
                    )
                else:
                    shutil.copy2(s, d)
        log_ok(f"Installed: {desc}")
    finally:
        if os.path.isdir(td):
            shutil.rmtree(td, ignore_errors=True)
            TMP_DIR = None


def shared_test_connection(url, headers=None, skip_env_vars=False):
    log_info(f"Testing connection: {url}")
    req = urllib.request.Request(
        url,
        stdjson.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if headers:
        for k, v in headers.items():
            if skip_env_vars and "${" in str(v):
                continue
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            if resp.status == 200:
                try:
                    d = stdjson.loads(body)
                    r = d.get("result", "")
                    result = (
                        r
                        if r
                        else (
                            "Reachable (Method not found, expected)"
                            if d.get("error", {}).get("code") == -32601
                            else "Connected"
                        )
                    )
                except:
                    result = "Response parsed"
                log_ok(f"Connection successful! {result}")
            else:
                log_error(f"Connection failed (HTTP {resp.status})")
    except urllib.error.HTTPError as e:
        log_error(f"Connection failed (HTTP {e.code})")
    except urllib.error.URLError as e:
        log_error(f"Connection failed: {e.reason}")


# ══════════════════════ Main Entry ══════════════════════


def validate_mcp_entries(entries):
    if not isinstance(entries, dict) or not entries:
        raise ValueError("mcp must be a non-empty object")
    for name, cfg in entries.items():
        if not isinstance(name, str) or not isinstance(cfg, dict):
            raise ValueError("Each MCP entry must be a named object")
        if not isinstance(cfg.get("url"), str) or not cfg["url"].startswith(("https://", "http://")):
            raise ValueError("Each MCP entry requires an HTTP(S) url")
        headers = cfg.get("headers", {})
        if not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
            raise ValueError("MCP headers must be a string mapping")


def client_paths(tool, scope):
    """Return the config and skill root in exactly one selected scope."""
    root = Path(PROJECT_DIR) if scope == "project" else Path.home()
    if tool in ("claude", "codebuddy"):
        family = "claude" if tool == "claude" else "codebuddy"
        if scope == "project":
            candidates = [root / ".mcp.json"]
            if family == "codebuddy":
                candidates.append(root / "mcp.json")
        elif family == "claude":
            candidates = [root / ".claude.json"]
        else:
            candidates = [root / ".codebuddy/.mcp.json", root / ".codebuddy/mcp.json", root / ".codebuddy.json"]
        config = next((p for p in candidates if p.is_file()), candidates[0])
        return config, root / f".{family}/skills"
    if tool == "codex":
        return root / ".codex/config.toml", root / ".agents/skills"
    if tool == "opencode":
        base = root if scope == "project" else root / ".config/opencode"
        candidates = [base / "opencode.jsonc", base / "opencode.json"]
        config = next((p for p in candidates if p.is_file()), candidates[0])
        skills = root / ".opencode/skills" if scope == "project" else base / "skills"
        return config, skills
    if tool == "gemini":
        return root / ".gemini/settings.json", root / ".gemini/skills"
    if tool == "zcode":
        config = ".zcode/config.json" if scope == "project" else ".zcode/cli/config.json"
        return root / config, root / ".zcode/skills"
    if scope != "user":
        raise ValueError(f"{tool} project scope is not supported; specify --user")
    paths = {
        "cline": (".cline/data/settings/cline_mcp_settings.json", ".cline/skills"),
        "codewhale": (".codewhale/mcp.json", ".codewhale/skills"),
    }
    config, skills = paths[tool]
    return root / config, root / skills


def load_client_config(path):
    if not path.is_file():
        return {}
    if path.suffix == ".toml":
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    else:
        text = path.read_text(encoding="utf-8-sig")
        try:
            data = commentjson.loads(text) if text.strip() else {}
        except Exception:
            # commentjson's parser exception can embed the original source text.
            raise ValueError("Invalid client JSON configuration") from None
    if not isinstance(data, dict):
        raise ValueError("Client config must be an object")
    return data


def write_client_config(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".toml":
        with path.open("wb") as stream:
            tomli_w.dump(data, stream)
    else:
        path.write_text(stdjson.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log_ok(f"Written: {path}")


def client_servers(tool, data, create=False):
    """Locate a client's server mapping, including ZCode's nested container."""
    keys = {
        "codex": ("mcp_servers",),
        "opencode": ("mcp",),
        "codewhale": ("servers",),
        "zcode": ("mcp", "servers"),
    }.get(tool, ("mcpServers",))
    if tool == "codewhale" and "servers" not in data and "mcpServers" in data:
        keys = ("mcpServers",)
    current = data
    for key in keys:
        current = current.setdefault(key, {}) if create else current.get(key, {})
        if not isinstance(current, dict):
            raise ValueError(f"{key} must be an object")
    return current


def prepare_client(tool, mcp_cfg, skill_url, scope):
    config, skills = client_paths(tool, scope)
    data = load_client_config(config)
    servers = client_servers(tool, data, create=True)
    if tool == "opencode" and "mcpServers" in data:
        legacy = data.pop("mcpServers")
        if not isinstance(legacy, dict):
            raise ValueError("mcpServers must be an object")
        for name, entry in legacy.items():
            servers.setdefault(name, entry)
    servers.update(client_servers(tool, wrap_mcp_entry(tool, mcp_cfg)))
    # Download and validate before changing client configuration.
    name = shared_download_zip(skill_url)
    target = skills / name
    shared_extract_and_install(SKILL_ZIP_PATH, str(target), f"{scope} {target}")
    if not (target / "SKILL.md").is_file():
        raise ValueError("Installed Skill has no root SKILL.md")
    write_client_config(config, data)
    log_ok(f"Skill files installed: {target}")
    log_info("Configuration written. Restart the client and verify Skill discovery and MCP connection there.")


def uninstall_tool(tool, scope):
    config, skills = client_paths(tool, scope)
    target = skills / "qpanda3-programming"
    root = Path(PROJECT_DIR) if scope == "project" else Path.home()
    # Resolve before any mutation or recursive removal, including Windows junctions.
    if target.exists():
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("Skill target resolves outside the selected scope")
        if target.is_symlink():
            raise ValueError("Refusing to recursively uninstall a linked Skill")
    data = load_client_config(config)
    servers = client_servers(tool, data)
    if "qpanda3-runtime" in servers:
        del servers["qpanda3-runtime"]
        write_client_config(config, data)
    else:
        log_info(f"qpanda3-runtime not found in {config}")
    if tool == "claude" and scope == "project":
        settings = Path(PROJECT_DIR) / ".claude/settings.local.json"
        if settings.is_file():
            choices = load_client_config(settings)
            changed = False
            for key in ("enabledMcpjsonServers", "disabledMcpjsonServers"):
                values = choices.get(key)
                if isinstance(values, list) and "qpanda3-runtime" in values:
                    choices[key] = [v for v in values if v != "qpanda3-runtime"]
                    changed = True
            if changed:
                write_client_config(settings, choices)
    if target.exists():
        shutil.rmtree(target)
        log_ok(f"Removed: {target}")
    log_info(f"Uninstall finished for {tool}, scope={scope}. Restart the client.")


def cmd_list():
    print("Supported AI coding tools:")
    for key, info in SUPPORTED_TOOLS.items():
        scope = "user only (--user)" if key in ("cline", "codewhale") else "project (default), user (--user)"
        print(f"  {key:12s} {info['name']:12s} {scope}")


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Configure QPanda3 MCP + programming Skill (project scope by default).",
        epilog='Example: uv run --locked python scripts/prepare_qpanda3_ai_kits.py prepare codex -k "QPANDA3_API_KEY"; cline and codewhale require --user.',
    )
    parser.add_argument("command", nargs="?", default="help", choices=("list", "prepare", "uninstall", "help"))
    parser.add_argument("tool", nargs="?", choices=tuple(SUPPORTED_TOOLS))
    credentials = parser.add_mutually_exclusive_group()
    credentials.add_argument("-k", "--apikey", help="literal API key or JSON input file path")
    credentials.add_argument("--api-key-env", metavar="NAME", help="reference this environment variable without saving its value")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("-p", "--project", action="store_const", const="project", dest="scope", help="project scope (default)")
    scope.add_argument("--user", action="store_const", const="user", dest="scope", help="explicitly use user scope")
    parser.set_defaults(scope="project")
    parser.add_argument("--project-dir", default=os.getcwd(), help="project directory (default: current working directory)")
    return parser


def main():
    global PROJECT_DIR
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "help":
        parser.print_help()
        return
    if args.command == "list":
        cmd_list()
        return
    PROJECT_DIR = str(Path(args.project_dir).expanduser().resolve())
    if not Path(PROJECT_DIR).is_dir():
        parser.error("--project-dir must be an existing directory")
    if args.command == "uninstall" and not args.tool:
        parser.error("uninstall requires one explicit tool; it never uninstalls all tools")
    tools = [args.tool] if args.tool else detect_installed_tools(args.scope)
    if not tools:
        parser.error("No tools detected; specify the tool explicitly")
    for tool in tools:
        if args.scope == "project" and tool in ("cline", "codewhale"):
            parser.error(f"{tool} requires --user")
    log_info(f"Scope: {args.scope}; project directory: {PROJECT_DIR}")
    if args.command == "uninstall":
        uninstall_tool(args.tool, args.scope)
        return
    if args.api_key_env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.api_key_env):
            parser.error("--api-key-env requires a valid environment variable name")
        if not os.environ.get(args.api_key_env):
            parser.error("The API key environment variable is unset or empty")
        if any(t not in ("claude", "codex", "codebuddy") for t in tools):
            parser.error("--api-key-env supports claude, codex and codebuddy")
        mcp_cfg = generate_mcp_entry("${" + args.api_key_env + "}", DEFAULT_MCP_URL)
        skill_url = DEFAULT_SKILL_URL
    elif args.apikey:
        mcp_cfg, skill_url = resolve_k_param(args.apikey)
        if mcp_cfg is None:
            mcp_cfg = generate_mcp_entry(args.apikey, DEFAULT_MCP_URL)
    else:
        parser.error("prepare requires -k/--apikey or --api-key-env")
    validate_mcp_entries(mcp_cfg)
    for tool in tools:
        log_info(f"Preparing {SUPPORTED_TOOLS[tool]['name']}")
        prepare_client(tool, mcp_cfg, skill_url, args.scope)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, stdjson.JSONDecodeError) as exc:
        # Parser exceptions may include config contents; do not echo credentials.
        log_error(f"Setup failed ({type(exc).__name__}); check input format, permissions and network")
        sys.exit(1)
