#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyqpanda3",
#     "json5>=0.17.3",
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
from contextlib import contextmanager
from copy import deepcopy
import json as stdjson
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import json5
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
            servers[name]["type"] = "remote"
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
    try:
        check_config_overrides(tool, scope)
    except ValueError:
        return False
    if tool == "codewhale" and scope == "project":
        log_error(
            "This script currently supports Codewhale with --user only. Use 'prepare codewhale -k KEY --user'."
        )
        return False
    if scope == "project":
        cmd = TOOL_COMMANDS.get(tool)
        if cmd and shutil.which(cmd):
            log_info(f"Detected {tool} by PATH; this does not verify MCP connectivity")
            return True
        log_warn(
            f"{SUPPORTED_TOOLS[tool]['name']} command not found in PATH ({cmd}), skipped"
        )
        return False
    paths = USER_CONFIG_DIRS.get(tool, [])
    for p in paths:
        if os.path.exists(os.path.expanduser(p)):
            log_info(f"Detected {tool} by default user configuration directory")
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
    parsed = urlparse(url)
    # Signed queries, fragments and userinfo must never enter logs.
    log_info(f"Downloading Skill from: {parsed.hostname or '(invalid host)'}")
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
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", SKILL_NAME):
        raise ValueError("Skill ZIP needs a safe basename")
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
            if len(z.infolist()) > 10000 or sum(i.file_size for i in z.infolist()) > 256 * 1024 * 1024:
                raise ValueError("Skill archive exceeds extraction limits")
            r = z.testzip()
            if r:
                log_error(f"Corrupt ZIP: {r}")
                sys.exit(1)
            for info in z.infolist():
                # ZipInfo normalizes separators on Windows; validate the raw name too.
                name = info.orig_filename
                parts = Path(name.replace("\\", "/")).parts
                if ".." in parts or name.startswith(("/", "\\")) or ":" in name:
                    raise ValueError("Unsafe archive path")
                if "\\" in name:
                    raise ValueError("Archive paths must use forward slashes")
            if any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in z.infolist()):
                raise ValueError("Linked archive entries are not supported")
            if not any(name == "SKILL.md" or name.endswith("/SKILL.md") for name in z.namelist()):
                raise ValueError("Skill archive contains no SKILL.md")
    except Exception as e:
        log_error(f"Invalid ZIP: {e}")
        sys.exit(1)
    s = os.path.getsize(zp)
    log_ok(f"Valid ZIP ({s // 1024}K)" if s >= 1024 else f"Valid ZIP ({s}B)")


@contextmanager
def installed_skill(zip_path, target, root):
    """Validate a fresh tree, replace the managed directory, restore on failure.

    Extraction and backups stay in repository .tmp, including for user-level
    installs on another volume. Copying a Skill is not a crash-atomic operation.
    """
    guard_path(target, root)
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="_skill_install.", dir=TMP_ROOT) as td:
        staging = Path(td) / "extract"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(staging)
        items = list(staging.iterdir())
        source = items[0] if len(items) == 1 and items[0].is_dir() else staging
        if not (source / "SKILL.md").is_file():
            raise ValueError("New Skill must contain a root SKILL.md (at most one wrapper directory)")
        previous = Path(td) / "previous"
        existed = target.exists()
        if existed:
            if not target.is_dir():
                raise ValueError("Skill destination must be a directory")
            shutil.copytree(target, previous, symlinks=True)
        try:
            guard_path(target, root)
            if existed:
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("build_index.py", "verify_sync.py"))
            yield
        except BaseException:
            try:
                # Do not follow a link introduced while installation was in progress.
                guard_path(target, root)
                if target.exists():
                    shutil.rmtree(target)
                if existed:
                    shutil.copytree(previous, target, symlinks=True)
            except (OSError, ValueError):
                if existed:
                    recovery = TMP_ROOT / ("skill-recovery-" + Path(td).name)
                    shutil.move(previous, recovery)
                    log_error(f"Skill restore failed; original retained at {recovery}")
                raise
            raise


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
    check_config_overrides(tool, scope)
    root = Path(PROJECT_DIR) if scope == "project" else Path.home().resolve()
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
    if tool == "codewhale":
        # Each default state path falls back independently in Codewhale v0.10.1.
        config = next((p for p in (config, ".deepseek/mcp.json") if (root / p).exists()), config)
        skills = next((p for p in (skills, ".deepseek/skills") if (root / p).exists()), skills)
        settings = next((p for p in (root / ".codewhale/config.toml", root / ".deepseek/config.toml") if p.exists()), None)
        if settings and "mcp_config_path" in load_client_config(settings):
            log_error("Codewhale mcp_config_path changes the supported layout; configure it manually")
            raise ValueError("Codewhale mcp_config_path override requires manual configuration")
    return root / config, root / skills


def check_config_overrides(tool, scope):
    """Refuse unsupported alternate roots instead of reporting a false success."""
    home = Path.home().resolve()
    defaults = {
        "codex": {"CODEX_HOME": home / ".codex"},
        "claude": {"CLAUDE_CONFIG_DIR": None},
        "gemini": {"GEMINI_CLI_HOME": home},
        "opencode": {"XDG_CONFIG_HOME": home / ".config", "OPENCODE_CONFIG": None,
                     "OPENCODE_CONFIG_DIR": None, "OPENCODE_CONFIG_CONTENT": None},
        "cline": {"CLINE_DIR": home / ".cline", "CLINE_DATA_DIR": home / ".cline/data",
                  "CLINE_MCP_SETTINGS_PATH": home / ".cline/data/settings/cline_mcp_settings.json"},
        "codewhale": {"CODEWHALE_HOME": None, "CODEWHALE_CONFIG_PATH": None,
                      "DEEPSEEK_HOME": None, "DEEPSEEK_CONFIG_PATH": None},
        "codebuddy": {"CODEBUDDY_CONFIG_DIR": home / ".codebuddy"},
    }
    user_root_only = {"CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME"}
    for name, default in defaults.get(tool, {}).items():
        if scope == "project" and name in user_root_only:
            continue
        value = os.environ.get(name)
        if value and (default is None or Path(value).expanduser().resolve() != default.resolve()):
            log_error(f"{name} changes the configuration layout; use the client's manual configuration")
            raise ValueError(f"{name} overrides the supported layout; configure this client manually")


def guard_path(path, root):
    """Reject linked files/parents, including Windows junctions, before mutation."""
    path, root = Path(path).absolute(), Path(root).resolve()
    if not path.resolve().is_relative_to(root):
        raise ValueError("Target resolves outside the selected scope")
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Linked configuration/Skill paths require manual configuration")
        if part == root:
            break


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def load_client_config(path):
    if not path.is_file():
        return {}
    if path.suffix == ".toml":
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    else:
        text = path.read_text(encoding="utf-8-sig")
        try:
            if not text.strip():
                data = {}
            else:
                try:
                    data = stdjson.loads(text, object_pairs_hook=unique_json_object)
                except stdjson.JSONDecodeError:
                    data = json5.loads(text, allow_duplicate_keys=False)
        except Exception:
            # Parser exceptions can embed the original source text.
            raise ValueError("Invalid client JSON configuration") from None
    if not isinstance(data, dict):
        raise ValueError("Client config must be an object")
    return data


def serialize_client_config(path, data):
    text = tomli_w.dumps(data) if path.suffix == ".toml" else stdjson.dumps(
        data, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=True
    ) + "\n"
    return text.encode("utf-8")


def atomic_write(path, content):
    """Stage next to the destination so os.replace works across drive layouts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".qpanda-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, name)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_client_config(path, data):
    # Serialize completely before creating directories or opening a target file.
    atomic_write(path, serialize_client_config(path, data))
    log_ok(f"Written: {path}")


def write_config_batch(changes, root, expected=None):
    prepared = [(path, serialize_client_config(path, data)) for path, data in changes]
    for path, _ in prepared:
        guard_path(path, root)
    if expected is not None:
        for path, before in expected.items():
            guard_path(path, root)
            if (path.read_bytes() if path.exists() else None) != before:
                raise ValueError("Client configuration changed during prepare; retry after closing the client")
    originals = {path: path.read_bytes() if path.exists() else None for path, _ in prepared}
    written = []
    try:
        for path, content in prepared:
            guard_path(path, root)
            atomic_write(path, content)
            written.append(path)
            log_ok(f"Written: {path}")
    except BaseException:
        try:
            for path in reversed(written):
                guard_path(path, root)
                if originals[path] is None:
                    path.unlink()
                else:
                    atomic_write(path, originals[path])
        except (OSError, ValueError):
            TMP_ROOT.mkdir(parents=True, exist_ok=True)
            recovery = Path(tempfile.mkdtemp(prefix="config-recovery-", dir=TMP_ROOT))
            manifest = []
            for index, (path, content) in enumerate(originals.items()):
                name = f"{index}{path.suffix}"
                if content is not None:
                    (recovery / name).write_bytes(content)
                manifest.append({"target": str(path), "backup": name if content is not None else None})
            (recovery / "manifest.json").write_text(stdjson.dumps(manifest, indent=2), encoding="utf-8")
            log_error(f"Config restore failed; originals retained at {recovery}")
            raise
        raise


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
    if tool == "opencode":
        return opencode_server_maps(data, create)[0]
    current = data
    for key in keys:
        current = current.setdefault(key, {}) if create else current.get(key, {})
        if not isinstance(current, dict):
            raise ValueError(f"{key} must be an object")
    return current


def opencode_server_maps(data, create=False):
    mcp = data.setdefault("mcp", {}) if create else data.get("mcp", {})
    if not isinstance(mcp, dict):
        raise ValueError("mcp must be an object")
    nested = mcp.get("servers")
    # A remote/local server may itself be named 'servers'.
    direct = isinstance(nested, dict) and any(k in nested and not isinstance(nested[k], dict) for k in ("type", "enabled"))
    if isinstance(nested, dict) and not direct:
        return [nested, mcp]
    return [mcp]


def scope_configs(tool, scope, selected):
    if tool == "opencode":
        base = Path(PROJECT_DIR) if scope == "project" else Path.home().resolve() / ".config/opencode"
        candidates = [base / name for name in ("opencode.json", "opencode.jsonc")]
        if scope == "project":
            candidates += [base / ".opencode" / name for name in ("opencode.json", "opencode.jsonc")]
        return list(dict.fromkeys([selected, *(p for p in candidates if p.exists())]))
    if tool == "codebuddy":
        base = Path(PROJECT_DIR) if scope == "project" else Path.home().resolve()
        names = (".mcp.json", "mcp.json") if scope == "project" else (".codebuddy/.mcp.json", ".codebuddy/mcp.json", ".codebuddy.json")
        return list(dict.fromkeys([selected, *(base / p for p in names if (base / p).exists())]))
    if tool == "zcode":
        root = Path(PROJECT_DIR) if scope == "project" else Path.home().resolve()
        fallback = root / ".agents/mcp.json"
        return [selected, *([fallback] if fallback.exists() else [])]
    return [selected]


def merge_managed_entry(old, new):
    if not isinstance(old, dict):
        raise ValueError("Existing same-name MCP entry must be an object")
    result = deepcopy(old)
    # Replace connection/authentication fields; preserve user control settings.
    for key in ("url", "httpUrl", "type", "transport", "command", "args", "env",
                "bearer_token_env_var", "oauth", "headers", "http_headers", "env_http_headers"):
        result.pop(key, None)
    for key, value in new.items():
        if key in ("enabled", "disabled", "autoApprove") and key in result:
            continue
        result[key] = deepcopy(value)
    # Unrelated custom headers survive, but stale authentication never does.
    for field in ("headers", "http_headers", "env_http_headers"):
        kept = {k: v for k, v in old.get(field, {}).items() if k.lower() not in ("x-api-key", "authorization")}
        kept.update(new.get(field, {}))
        if kept:
            result[field] = kept
    return result


def normalize_opencode_legacy(data):
    if "mcpServers" not in data:
        return
    legacy = data.pop("mcpServers", {})
    if not isinstance(legacy, dict):
        raise ValueError("mcpServers must be an object")
    servers = client_servers("opencode", data, create=True)
    for name, entry in legacy.items():
        if name in servers:
            continue
        if not isinstance(entry, dict):
            raise ValueError("Unrecognized legacy OpenCode MCP entry")
        item = deepcopy(entry)
        if isinstance(item.get("url"), str):
            item["type"] = "remote"
            allowed = {"type", "url", "headers", "enabled", "disabled", "timeout", "oauth"}
        elif isinstance(item.get("command"), (str, list)):
            command = item["command"]
            args = item.pop("args", [])
            if not isinstance(args, list) or not all(isinstance(v, str) for v in args):
                raise ValueError("Invalid legacy command args")
            item["command"] = ([command] if isinstance(command, str) else command) + args
            if not item["command"] or not all(isinstance(v, str) for v in item["command"]):
                raise ValueError("Invalid legacy command")
            item["type"] = "local"
            if "env" in item:
                item["environment"] = item.pop("env")
            allowed = {"type", "command", "environment", "enabled", "disabled", "timeout"}
        else:
            raise ValueError("Unrecognized legacy OpenCode MCP entry")
        if "disabled" in item:
            item.setdefault("enabled", not item.pop("disabled"))
        if set(item) - allowed:
            raise ValueError("Legacy OpenCode MCP has unsupported fields; migrate it manually")
        servers[name] = item


def prepare_client(tool, mcp_cfg, skill_url, scope):
    config, skills = client_paths(tool, scope)
    root = Path(PROJECT_DIR) if scope == "project" else Path.home().resolve()
    paths = scope_configs(tool, scope, config)
    for path in [*paths, skills]:
        guard_path(path, root)
    expected = {path: path.read_bytes() if path.exists() else None for path in paths}
    records = {path: load_client_config(path) for path in paths}
    data = records[config]
    if tool == "opencode":
        for value in records.values():
            normalize_opencode_legacy(value)
    servers = client_servers(tool, data, create=True)
    if tool == "zcode" and not servers and len(paths) > 1:
        fallback = records[paths[1]].get("mcpServers", {})
        if not isinstance(fallback, dict):
            raise ValueError("ZCode fallback mcpServers must be an object")
        servers.update(deepcopy(fallback))
    if tool == "claude" and scope == "project" and not config.exists():
        # Creating .mcp.json also changes CodeBuddy's effective project source.
        fallback = Path(PROJECT_DIR) / "mcp.json"
        guard_path(fallback, root)
        if fallback.exists():
            expected[fallback] = fallback.read_bytes()
            prior = load_client_config(fallback).get("mcpServers", {})
            if not isinstance(prior, dict):
                raise ValueError("CodeBuddy fallback mcpServers must be an object")
            servers.update(deepcopy(prior))
    incoming = client_servers(tool, wrap_mcp_entry(tool, mcp_cfg))
    if tool == "codebuddy":
        for entry in incoming.values():
            for value in entry.get("headers", {}).values():
                for name in re.findall(r"\$\{([^}:]+)(?::-.*?)?\}", value):
                    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
                        raise ValueError("CodeBuddy header environment variables must be uppercase")
    changes = []
    for path, value in records.items():
        if tool == "zcode" and path != config:
            continue  # Preserve the shared fallback; native copy now retains its effective services.
        maps = opencode_server_maps(value) if tool == "opencode" else [client_servers(tool, value)]
        if len(maps) > 1:
            for name, new in incoming.items():
                if name in maps[0] and name in maps[1] and merge_managed_entry(maps[0][name], new) != merge_managed_entry(maps[1][name], new):
                    raise ValueError("Conflicting OpenCode MCP containers require manual resolution")
        changed = path == config or (tool == "opencode" and value != load_client_config(path))
        for mapping in maps:
            for name, new in incoming.items():
                if name in mapping or (path == config and mapping is maps[0]):
                    mapping[name] = merge_managed_entry(mapping.get(name, {}), new)
                    changed = True
        if changed:
            serialize_client_config(path, value)  # Fail before changing the Skill too.
            changes.append((path, value))
    name = shared_download_zip(skill_url)
    target = skills / name
    with installed_skill(SKILL_ZIP_PATH, target, root):
        write_config_batch(changes, root, expected)
    log_ok(f"Skill files installed: {target}")
    log_info("Configuration written. Restart the client and verify Skill discovery and MCP connection there.")


def uninstall_tool(tool, scope, skill_only=False):
    config, skills = client_paths(tool, scope)
    target = skills / "qpanda3-programming"
    root = Path(PROJECT_DIR) if scope == "project" else Path.home().resolve()
    guard_path(target, root)
    changes = []
    paths = [] if skill_only else scope_configs(tool, scope, config)
    for path in paths:
        guard_path(path, root)
        data = load_client_config(path)
        if tool == "opencode":
            maps = [*opencode_server_maps(data), data.get("mcpServers", {})]
        elif tool == "zcode" and path != config:
            maps = [data.get("mcpServers", {})]
        else:
            maps = [client_servers(tool, data)]
        changed = False
        for mapping in maps:
            if not isinstance(mapping, dict):
                raise ValueError("MCP server container must be an object")
            if "qpanda3-runtime" in mapping:
                del mapping["qpanda3-runtime"]
                changed = True
        if changed:
            changes.append((path, data))
    if tool in ("claude", "codebuddy") and scope == "project" and not skill_only:
        log_warn("Removing project MCP also affects clients sharing .mcp.json; use --skill-only to retain MCP")
    if tool == "claude" and scope == "project" and not skill_only:
        settings = Path(PROJECT_DIR) / ".claude/settings.local.json"
        guard_path(settings, root)
        if settings.is_file():
            choices = load_client_config(settings)
            changed = False
            for key in ("enabledMcpjsonServers", "disabledMcpjsonServers"):
                values = choices.get(key)
                if isinstance(values, list) and "qpanda3-runtime" in values:
                    choices[key] = [v for v in values if v != "qpanda3-runtime"]
                    changed = True
            if changed:
                changes.append((settings, choices))
    write_config_batch(changes, root)
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
    parser.add_argument("--skill-only", action="store_true", help="uninstall only the default Skill, retaining shared MCP configuration")
    return parser


def main():
    global PROJECT_DIR
    parser = build_parser()
    args = parser.parse_args()
    if args.skill_only and args.command != "uninstall":
        parser.error("--skill-only requires uninstall")
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
        uninstall_tool(args.tool, args.scope, args.skill_only)
        return
    if args.api_key_env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.api_key_env):
            parser.error("--api-key-env requires a valid environment variable name")
        if "codebuddy" in tools and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", args.api_key_env):
            parser.error("CodeBuddy requires an uppercase environment variable name ([A-Z_][A-Z0-9_]*)")
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
