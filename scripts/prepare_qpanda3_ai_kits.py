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
"""

import atexit
import json as stdjson
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

import commentjson
import tomli_w
import tomllib

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_ZIP = None
TMP_DIR = None


def cleanup():
    if TMP_ZIP and os.path.isfile(TMP_ZIP):
        os.unlink(TMP_ZIP)
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
    - If a file with that name exists next to the script, parse its JSON for mcp config + skill_url
    - Otherwise treat as API key string, use global defaults
    Returns (mcp_cfg: dict | None, skill_url: str)
    """
    filepath = os.path.join(SCRIPT_DIR, k_value)
    if os.path.isfile(filepath):
        try:
            with open(filepath, encoding="utf-8") as f:
                data = stdjson.load(f)
            mcp_cfg = data.get("mcp")
            skill_url = data.get("skill_url", DEFAULT_SKILL_URL)
            log_info(f"Read config from: {filepath}")
            return mcp_cfg, skill_url
        except Exception as e:
            log_warn(f"Failed to parse config ({e}), falling back to API key mode")
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
    elif tool == "claude":
        servers = {}
        for name, cfg in entry.items():
            servers[name] = dict(cfg)
            servers[name].setdefault("type", "http")
            servers[name].setdefault("enabled", True)
            servers[name].setdefault("transport", "streamable-http")
        return {"mcpServers": servers}
    elif tool == "codex":
        return {"mcp_servers": entry}
    elif tool == "gemini":
        return {"mcpServers": entry}
    elif tool == "codebuddy":
        return {"mcpServers": entry}
    elif tool == "cline":
        servers = {}
        for name, cfg in entry.items():
            servers[name] = {
                "disabled": False,
                "autoApprove": [],
                "transport": {
                    "type": "streamableHttp",
                    "url": cfg["url"],
                    "headers": cfg.get("headers", {}),
                },
            }
        return {"mcpServers": servers}
    elif tool == "codewhale":
        servers = {}
        for name, cfg in entry.items():
            servers[name] = {
                "url": cfg["url"],
                "headers": cfg.get("headers", {}),
                "command": None,
                "args": [],
                "env": {},
                "connect_timeout": None,
                "execute_timeout": None,
                "read_timeout": None,
                "disabled": False,
                "enabled": True,
                "required": False,
                "enabled_tools": [],
                "disabled_tools": [],
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
            "Cline only supports user-level configuration. Use 'prepare cline -k KEY' without -p."
        )
        return False
    if tool == "codewhale" and scope == "project":
        log_error(
            "Codewhale only supports user-level configuration. Use 'prepare codewhale -k KEY' without -p."
        )
        return False
    if tool == "zcode" and scope == "project":
        log_error(
            "ZCode only supports user-level configuration. Use 'prepare zcode -k KEY' without -p."
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
    tmp = tempfile.NamedTemporaryFile(prefix="_skill.", suffix=".zip", delete=False)
    TMP_ZIP = tmp.name
    tmp.close()
    log_info(f"Downloading: {url}")
    try:
        urllib.request.urlretrieve(url, TMP_ZIP)
    except Exception as e:
        log_error(f"Download failed: {e}")
        sys.exit(1)
    log_ok("Download complete")
    shared_validate_zip(TMP_ZIP)
    SKILL_NAME = os.path.splitext(os.path.basename(url))[0]
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
    except Exception as e:
        log_error(f"Invalid ZIP: {e}")
        sys.exit(1)
    s = os.path.getsize(zp)
    log_ok(f"Valid ZIP ({s // 1024}K)" if s >= 1024 else f"Valid ZIP ({s}B)")


def shared_extract_and_install(zip_path, target, desc):
    global TMP_DIR
    os.makedirs(target, exist_ok=True)
    td = tempfile.mkdtemp(prefix="_skill_extract.")
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


# ══════════════════════ Shared Uninstall Helpers ══════════════════════


def uninstall_json_entry(fp, mcp_key, server_name):
    if not os.path.isfile(fp):
        return None
    try:
        with open(fp, "r", encoding="utf-8") as f:
            ct = f.read()
        if not ct.strip():
            return None
        try:
            data = commentjson.loads(ct)
        except Exception:
            try:
                data = stdjson.loads(ct)
            except:
                return None
        se = data.get(mcp_key, {})
        if server_name not in se:
            return "NOT_FOUND"
        del se[server_name]
        data[mcp_key] = se
        with open(fp, "w", encoding="utf-8") as f:
            stdjson.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return "REMOVED"
    except Exception as e:
        return f"ERROR: {e}"


def uninstall_mcp_arrays(fp, server_name, array_keys):
    if not os.path.isfile(fp):
        return None
    try:
        with open(fp, "r", encoding="utf-8") as f:
            ct = f.read()
        if not ct.strip():
            return None
        try:
            data = commentjson.loads(ct)
        except:
            try:
                data = stdjson.loads(ct)
            except:
                return None
        changed = False
        for k in array_keys:
            arr = data.get(k)
            if isinstance(arr, list) and server_name in arr:
                arr.remove(server_name)
                changed = True
        if changed:
            with open(fp, "w", encoding="utf-8") as f:
                stdjson.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            return "REMOVED"
        return "NOT_FOUND"
    except Exception as e:
        return f"ERROR: {e}"


def uninstall_skill_dir(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
        return True
    return False


def uninstall_print_result(label, r):
    if r is None:
        log_warn(f"{label} not found, skipped")
    elif r == "NOT_FOUND":
        log_warn(f"{label} not found")
    elif r == "REMOVED":
        log_ok(f"Removed from {label}")


def uninstall_toml_entry(fp, mcp_key, server_name):
    if not os.path.isfile(fp):
        return None
    try:
        with open(fp, "rb") as f:
            data = tomllib.load(f)
        servers = data.get(mcp_key, {})
        if server_name not in servers:
            return "NOT_FOUND"
        del servers[server_name]
        with open(fp, "wb") as f:
            tomli_w.dump(data, f)
        return "REMOVED"
    except Exception as e:
        return f"ERROR: {e}"


# ══════════════════════ Tool Modules ══════════════════════

# ---------- OpenCode ----------


def opencode_mcp_merge():
    os.makedirs(OC_CONFIG_DIR, exist_ok=True)
    if os.path.isfile(OC_CONFIG_FILE):
        with open(OC_CONFIG_FILE, encoding="utf-8") as f:
            ct = f.read()
        try:
            ex = commentjson.loads(ct) if ct.strip() else {}
        except:
            ex = stdjson.loads(ct) if ct.strip() else {}
        nd = stdjson.loads(OC_NORMALIZED_JSON)
        se = ex.get("mcp") or ex.get("mcpServers", {})
        for n, e in nd["mcp"].items():
            se[n] = e
        ex["mcp"] = se
        ex.pop("mcpServers", None)
        merged = stdjson.dumps(ex, indent=2, ensure_ascii=False)
    else:
        merged = OC_NORMALIZED_JSON
    with open(OC_CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(merged)
        log_ok(f"Written: {OC_CONFIG_FILE}")


def run_prepare_opencode(mcp_cfg: dict, skill_url: str, scope: str):
    global \
        OC_CONFIG_DIR, \
        OC_CONFIG_FILE, \
        OC_NORMALIZED_JSON, \
        OC_SKILL_IG, \
        OC_SKILL_IP, \
        OC_SKILL_PR
    log_info("Configuring OpenCode MCP")
    if scope == "project":
        OC_CONFIG_DIR = os.path.join(SCRIPT_DIR, ".opencode")
    else:
        OC_CONFIG_DIR = os.path.expanduser("~/.config/opencode")
    OC_CONFIG_FILE = os.path.join(OC_CONFIG_DIR, "opencode.jsonc")
    OC_NORMALIZED_JSON = stdjson.dumps(
        wrap_mcp_entry("opencode", mcp_cfg), indent=2, ensure_ascii=False
    )
    opencode_mcp_merge()

    log_info("Installing QPanda3 Skill")
    SKILL_NAME = shared_download_zip(skill_url)
    if scope == "project":
        target = os.path.join(SCRIPT_DIR, ".opencode", "skills", SKILL_NAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        OC_SKILL_IG = False
        OC_SKILL_IP = True
        OC_SKILL_PR = SCRIPT_DIR
    else:
        target = os.path.expanduser(f"~/.config/opencode/skills/{SKILL_NAME}")
        OC_SKILL_IG = True
        OC_SKILL_IP = False
    shared_extract_and_install(SKILL_ZIP_PATH, target, f"{scope} {target}")

    if globals().get("OC_SKILL_IG") or globals().get("OC_SKILL_IP"):
        f = False
        if OC_SKILL_IG and os.path.isfile(
            os.path.expanduser(f"~/.config/opencode/skills/{SKILL_NAME}/SKILL.md")
        ):
            f = True
        if OC_SKILL_IP and os.path.isfile(
            os.path.join(OC_SKILL_PR, ".opencode", "skills", SKILL_NAME, "SKILL.md")
        ):
            f = True
        if f:
            log_ok(f"Skill {SKILL_NAME} installed!")
            print("┌────────────────────────────────────────")
            print(f"│  Skill: {SKILL_NAME}")
            if OC_SKILL_IG:
                print(
                    f"│  global: {os.path.expanduser(f'~/.config/opencode/skills/{SKILL_NAME}/')}"
                )
            if OC_SKILL_IP:
                print(
                    f"│  project: {os.path.join(OC_SKILL_PR, '.opencode', 'skills', SKILL_NAME)}/"
                )
            print("│  Tip: restart OpenCode")
            print("└────────────────────────────────────────")
        else:
            log_error("Verification failed")
            sys.exit(1)


def run_uninstall_opencode():
    sn = "qpanda3-runtime"
    skn = "qpanda3-programming"
    log_info("Uninstalling OpenCode MCP configuration")
    for p, lb in [
        (os.path.expanduser("~/.config/opencode/opencode.jsonc"), "OpenCode global"),
        (os.path.join(SCRIPT_DIR, ".opencode", "opencode.jsonc"), "OpenCode project"),
    ]:
        uninstall_print_result(lb, uninstall_json_entry(p, "mcp", sn))
    print()
    log_info("Uninstalling Skill")
    for b, lb in [
        (os.path.expanduser("~/.config/opencode/skills"), "global"),
        (os.path.join(SCRIPT_DIR, ".opencode", "skills"), "project"),
    ]:
        t = os.path.join(b, skn)
        if uninstall_skill_dir(t):
            log_ok(f"Removed: {lb}")
        else:
            log_warn(f"{lb} not found")
    print()
    log_ok("Uninstall complete! Restart OpenCode.")


# ---------- Claude Code ----------


def claude_mcp_merge():
    os.makedirs(CL_CONFIG_DIR, exist_ok=True)
    if os.path.isfile(CL_CONFIG_FILE):
        with open(CL_CONFIG_FILE, encoding="utf-8") as f:
            ct = f.read()
        ex = stdjson.loads(ct) if ct.strip() else {}
        nd = stdjson.loads(CL_NORMALIZED_JSON)
        if "mcpServers" not in ex:
            ex["mcpServers"] = {}
        for n, e in nd["mcpServers"].items():
            ex["mcpServers"][n] = e
        merged = stdjson.dumps(ex, indent=2, ensure_ascii=False)
    else:
        merged = CL_NORMALIZED_JSON
    with open(CL_CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(merged)
        log_ok(f"Written: {CL_CONFIG_FILE}")


def run_prepare_claude(mcp_cfg: dict, skill_url: str, scope: str):
    global \
        CL_CONFIG_DIR, \
        CL_CONFIG_FILE, \
        CL_NORMALIZED_JSON, \
        CL_SKILL_IG, \
        CL_SKILL_IP, \
        CL_SKILL_PR
    log_info("Configuring Claude Code MCP")
    if scope == "project":
        CL_CONFIG_DIR = SCRIPT_DIR
        CL_CONFIG_FILE = os.path.join(CL_CONFIG_DIR, ".mcp.json")
    else:
        CL_CONFIG_DIR = os.path.expanduser("~")
        CL_CONFIG_FILE = os.path.join(CL_CONFIG_DIR, ".claude.json")
    CL_NORMALIZED_JSON = stdjson.dumps(
        wrap_mcp_entry("claude", mcp_cfg), indent=2, ensure_ascii=False
    )
    claude_mcp_merge()

    log_info("Installing QPanda3 Skill")
    SKILL_NAME = shared_download_zip(skill_url)
    if scope == "project":
        target = os.path.join(SCRIPT_DIR, ".claude", "skills", SKILL_NAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        CL_SKILL_IG = False
        CL_SKILL_IP = True
        CL_SKILL_PR = SCRIPT_DIR
    else:
        target = os.path.expanduser(f"~/.claude/skills/{SKILL_NAME}")
        CL_SKILL_IG = True
        CL_SKILL_IP = False
    shared_extract_and_install(SKILL_ZIP_PATH, target, f"{scope} {target}")

    f = False
    if CL_SKILL_IG and os.path.isfile(
        os.path.expanduser(f"~/.claude/skills/{SKILL_NAME}/SKILL.md")
    ):
        f = True
    if CL_SKILL_IP and os.path.isfile(
        os.path.join(CL_SKILL_PR, ".claude", "skills", SKILL_NAME, "SKILL.md")
    ):
        f = True
    if f:
        log_ok(f"Skill {SKILL_NAME} installed!")
        print("┌────────────────────────────────────────")
        print(f"│  Skill: {SKILL_NAME}")
        if CL_SKILL_IG:
            print(f"│  global: {os.path.expanduser(f'~/.claude/skills/{SKILL_NAME}/')}")
        if CL_SKILL_IP:
            print(
                f"│  project: {os.path.join(CL_SKILL_PR, '.claude', 'skills', SKILL_NAME)}/"
            )
        print("│  Tip: restart Claude Code")
        print("└────────────────────────────────────────")
    else:
        log_error("Verification failed")
        sys.exit(1)


def run_uninstall_claude():
    sn = "qpanda3-runtime"
    skn = "qpanda3-programming"
    log_info("Uninstalling Claude Code MCP configuration")
    uninstall_print_result(
        "Claude global",
        uninstall_json_entry(os.path.expanduser("~/.claude.json"), "mcpServers", sn),
    )
    for base in (SCRIPT_DIR,):
        p = os.path.join(base, ".mcp.json")
        uninstall_print_result(
            "Claude project", uninstall_json_entry(p, "mcpServers", sn)
        )
    for base in (SCRIPT_DIR,):
        ls = os.path.join(base, ".claude", "settings.local.json")
        r = uninstall_mcp_arrays(
            ls, sn, ["enabledMcpjsonServers", "disabledMcpjsonServers"]
        )
        if r == "REMOVED":
            log_ok(f"Removed {sn} from settings.local.json")
    print()
    log_info("Uninstalling Skill")
    for b, lb in [
        (os.path.expanduser("~/.claude/skills"), "global"),
        (os.path.join(SCRIPT_DIR, ".claude", "skills"), "project"),
    ]:
        t = os.path.join(b, skn)
        if uninstall_skill_dir(t):
            log_ok(f"Removed: {lb}")
        else:
            log_warn(f"{lb} not found")
    print()
    log_ok("Uninstall complete! Restart Claude Code.")


# ---------- Codex ----------


def codex_mcp_merge():
    os.makedirs(CDX_CONFIG_DIR, exist_ok=True)
    nd = stdjson.loads(CDX_NORMALIZED_JSON)
    servers = nd.get("mcp_servers", {})
    existing = {}
    if os.path.isfile(CDX_CONFIG_FILE):
        try:
            with open(CDX_CONFIG_FILE, "rb") as f:
                existing = tomllib.load(f)
        except FileNotFoundError:
            pass
    ms = existing.get("mcp_servers", {})
    for n, e in servers.items():
        ms[n] = e
    existing["mcp_servers"] = ms
    for sn in existing.get("mcp_servers", {}):
        cfg = existing["mcp_servers"][sn]
        if "headers" in cfg:
            cfg["http_headers"] = cfg.pop("headers")
        for skip in ("type", "transport"):
            cfg.pop(skip, None)
    with open(CDX_CONFIG_FILE, "wb") as f:
        tomli_w.dump(existing, f)
        log_ok(f"Written: {CDX_CONFIG_FILE}")


def run_prepare_codex(mcp_cfg: dict, skill_url: str, scope: str):
    global \
        CDX_CONFIG_DIR, \
        CDX_CONFIG_FILE, \
        CDX_NORMALIZED_JSON, \
        CDX_SKILL_IG, \
        CDX_SKILL_IP, \
        CDX_SKILL_PR
    log_info("Configuring Codex MCP")
    if scope == "project":
        CDX_CONFIG_DIR = os.path.join(SCRIPT_DIR, ".codex")
    else:
        CDX_CONFIG_DIR = os.path.expanduser("~/.codex")
    CDX_CONFIG_FILE = os.path.join(CDX_CONFIG_DIR, "config.toml")
    CDX_NORMALIZED_JSON = stdjson.dumps(
        wrap_mcp_entry("codex", mcp_cfg), indent=2, ensure_ascii=False
    )
    codex_mcp_merge()

    log_info("Installing QPanda3 Skill")
    SKILL_NAME = shared_download_zip(skill_url)
    if scope == "project":
        target = os.path.join(SCRIPT_DIR, ".codex", "skills", SKILL_NAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        CDX_SKILL_IG = False
        CDX_SKILL_IP = True
        CDX_SKILL_PR = SCRIPT_DIR
    else:
        target = os.path.expanduser(f"~/.codex/skills/{SKILL_NAME}")
        CDX_SKILL_IG = True
        CDX_SKILL_IP = False
    shared_extract_and_install(SKILL_ZIP_PATH, target, f"{scope} {target}")

    f = False
    if CDX_SKILL_IG and os.path.isfile(
        os.path.expanduser(f"~/.codex/skills/{SKILL_NAME}/SKILL.md")
    ):
        f = True
    if CDX_SKILL_IP and os.path.isfile(
        os.path.join(CDX_SKILL_PR, ".codex", "skills", SKILL_NAME, "SKILL.md")
    ):
        f = True
    if f:
        log_ok(f"Skill {SKILL_NAME} installed!")
        print("┌────────────────────────────────────────")
        print(f"│  Skill: {SKILL_NAME}")
        if CDX_SKILL_IG:
            print(f"│  global: {os.path.expanduser(f'~/.codex/skills/{SKILL_NAME}/')}")
        if CDX_SKILL_IP:
            print(
                f"│  project: {os.path.join(CDX_SKILL_PR, '.codex', 'skills', SKILL_NAME)}/"
            )
        print("│  Tip: restart Codex")
        print("└────────────────────────────────────────")
    else:
        log_error("Verification failed")
        sys.exit(1)


def run_uninstall_codex():
    sn = "qpanda3-runtime"
    skn = "qpanda3-programming"
    log_info("Uninstalling Codex MCP configuration")
    for p, lb in [
        (os.path.expanduser("~/.codex/config.toml"), "Codex global"),
        (os.path.join(SCRIPT_DIR, ".codex", "config.toml"), "Codex project"),
    ]:
        uninstall_print_result(lb, uninstall_toml_entry(p, "mcp_servers", sn))
    print()
    log_info("Uninstalling Skill")
    for b, lb in [
        (os.path.expanduser("~/.codex/skills"), "global"),
        (os.path.join(SCRIPT_DIR, ".codex", "skills"), "project"),
    ]:
        t = os.path.join(b, skn)
        if uninstall_skill_dir(t):
            log_ok(f"Removed: {lb}")
        else:
            log_warn(f"{lb} not found")
    print()
    log_ok("Uninstall complete! Restart Codex.")


# ---------- Gemini ----------


def gemini_mcp_merge():
    os.makedirs(GM_CONFIG_DIR, exist_ok=True)
    if os.path.isfile(GM_CONFIG_FILE):
        with open(GM_CONFIG_FILE, encoding="utf-8") as f:
            ct = f.read()
        ex = stdjson.loads(ct) if ct.strip() else {}
        nd = stdjson.loads(GM_NORMALIZED_JSON)
        if "mcpServers" not in ex:
            ex["mcpServers"] = {}
        for n, e in nd["mcpServers"].items():
            ex["mcpServers"][n] = e
        merged = stdjson.dumps(ex, indent=2, ensure_ascii=False)
    else:
        merged = GM_NORMALIZED_JSON
    with open(GM_CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(merged)
        log_ok(f"Written: {GM_CONFIG_FILE}")


def run_prepare_gemini(mcp_cfg: dict, skill_url: str, scope: str):
    global \
        GM_CONFIG_DIR, \
        GM_CONFIG_FILE, \
        GM_NORMALIZED_JSON, \
        GM_SKILL_IG, \
        GM_SKILL_IP, \
        GM_SKILL_PR
    log_info("Configuring Gemini CLI MCP")
    if scope == "project":
        GM_CONFIG_DIR = os.path.join(SCRIPT_DIR, ".gemini")
    else:
        GM_CONFIG_DIR = os.path.expanduser("~/.gemini")
    GM_CONFIG_FILE = os.path.join(GM_CONFIG_DIR, "settings.json")
    GM_NORMALIZED_JSON = stdjson.dumps(
        wrap_mcp_entry("gemini", mcp_cfg), indent=2, ensure_ascii=False
    )
    gemini_mcp_merge()

    log_info("Installing QPanda3 Skill")
    SKILL_NAME = shared_download_zip(skill_url)
    if scope == "project":
        target = os.path.join(SCRIPT_DIR, ".gemini", "skills", SKILL_NAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        GM_SKILL_IG = False
        GM_SKILL_IP = True
        GM_SKILL_PR = SCRIPT_DIR
    else:
        target = os.path.expanduser(f"~/.gemini/skills/{SKILL_NAME}")
        GM_SKILL_IG = True
        GM_SKILL_IP = False
    shared_extract_and_install(SKILL_ZIP_PATH, target, f"{scope} {target}")

    f = False
    if GM_SKILL_IG and os.path.isfile(
        os.path.expanduser(f"~/.gemini/skills/{SKILL_NAME}/SKILL.md")
    ):
        f = True
    if GM_SKILL_IP and os.path.isfile(
        os.path.join(GM_SKILL_PR, ".gemini", "skills", SKILL_NAME, "SKILL.md")
    ):
        f = True
    if f:
        log_ok(f"Skill {SKILL_NAME} installed!")
        print("┌────────────────────────────────────────")
        print(f"│  Skill: {SKILL_NAME}")
        if GM_SKILL_IG:
            print(f"│  user: {os.path.expanduser(f'~/.gemini/skills/{SKILL_NAME}/')}")
        if GM_SKILL_IP:
            print(
                f"│  project: {os.path.join(GM_SKILL_PR, '.gemini', 'skills', SKILL_NAME)}/"
            )
        print("│  Tip: restart Gemini CLI")
        print("└────────────────────────────────────────")
    else:
        log_error("Verification failed")
        sys.exit(1)


def run_uninstall_gemini():
    sn = "qpanda3-runtime"
    skn = "qpanda3-programming"
    log_info("Uninstalling Gemini MCP configuration")
    for p, lb in [
        (os.path.expanduser("~/.gemini/settings.json"), "Gemini user"),
        (os.path.join(SCRIPT_DIR, ".gemini", "settings.json"), "Gemini project"),
    ]:
        uninstall_print_result(lb, uninstall_json_entry(p, "mcpServers", sn))
    print()
    log_info("Uninstalling Skill")
    for b, lb in [
        (os.path.expanduser("~/.gemini/skills"), "user"),
        (os.path.join(SCRIPT_DIR, ".gemini", "skills"), "project"),
    ]:
        t = os.path.join(b, skn)
        if uninstall_skill_dir(t):
            log_ok(f"Removed: {lb}")
        else:
            log_warn(f"{lb} not found")
    print()
    log_ok("Uninstall complete! Restart Gemini CLI.")


# ---------- CodeBuddy ----------


def cdbd_find_user():
    for f in ["~/.codebuddy/.mcp.json", "~/.codebuddy/mcp.json", "~/.codebuddy.json"]:
        p = os.path.expanduser(f)
        if os.path.isfile(p):
            return p
    return None


def cdbd_find_project():
    for f in [
        os.path.join(SCRIPT_DIR, ".mcp.json"),
        os.path.join(SCRIPT_DIR, "mcp.json"),
    ]:
        if os.path.isfile(f):
            return f
    return None


def cdbd_mcp_merge():
    os.makedirs(CB_CONFIG_DIR, exist_ok=True)
    if os.path.isfile(CB_CONFIG_FILE):
        with open(CB_CONFIG_FILE, encoding="utf-8") as f:
            ct = f.read()
        try:
            ex = commentjson.loads(ct) if ct.strip() else {}
        except:
            ex = stdjson.loads(ct) if ct.strip() else {}
        nd = stdjson.loads(CB_NORMALIZED_JSON)
        if "mcpServers" not in ex:
            ex["mcpServers"] = {}
        for n, e in nd["mcpServers"].items():
            ex["mcpServers"][n] = e
        merged = stdjson.dumps(ex, indent=2, ensure_ascii=False)
    else:
        merged = CB_NORMALIZED_JSON
    with open(CB_CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(merged)
        log_ok(f"Written: {CB_CONFIG_FILE}")


def run_prepare_codebuddy(mcp_cfg: dict, skill_url: str, scope: str):
    global \
        CB_CONFIG_DIR, \
        CB_CONFIG_FILE, \
        CB_NORMALIZED_JSON, \
        CB_SKILL_IG, \
        CB_SKILL_IP, \
        CB_SKILL_PR
    log_info("Configuring CodeBuddy MCP")
    if scope == "project":
        f = cdbd_find_project()
        CB_CONFIG_FILE = f if f else os.path.join(SCRIPT_DIR, ".mcp.json")
    else:
        f = cdbd_find_user()
        CB_CONFIG_FILE = f if f else os.path.expanduser("~/.codebuddy/.mcp.json")
    CB_CONFIG_DIR = os.path.dirname(CB_CONFIG_FILE)
    CB_NORMALIZED_JSON = stdjson.dumps(
        wrap_mcp_entry("codebuddy", mcp_cfg), indent=2, ensure_ascii=False
    )
    cdbd_mcp_merge()

    log_info("Installing QPanda3 Skill")
    SKILL_NAME = shared_download_zip(skill_url)
    if scope == "project":
        target = os.path.join(SCRIPT_DIR, ".codebuddy", "skills", SKILL_NAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        CB_SKILL_IG = False
        CB_SKILL_IP = True
        CB_SKILL_PR = SCRIPT_DIR
    else:
        target = os.path.expanduser(f"~/.codebuddy/skills/{SKILL_NAME}")
        CB_SKILL_IG = True
        CB_SKILL_IP = False
    shared_extract_and_install(SKILL_ZIP_PATH, target, f"{scope} {target}")

    f = False
    if CB_SKILL_IG and os.path.isfile(
        os.path.expanduser(f"~/.codebuddy/skills/{SKILL_NAME}/SKILL.md")
    ):
        f = True
    if CB_SKILL_IP and os.path.isfile(
        os.path.join(CB_SKILL_PR, ".codebuddy", "skills", SKILL_NAME, "SKILL.md")
    ):
        f = True
    if f:
        log_ok(f"Skill {SKILL_NAME} installed!")
        print("┌────────────────────────────────────────")
        print(f"│  Skill: {SKILL_NAME}")
        if CB_SKILL_IG:
            print(
                f"│  user: {os.path.expanduser(f'~/.codebuddy/skills/{SKILL_NAME}/')}"
            )
        if CB_SKILL_IP:
            print(
                f"│  project: {os.path.join(CB_SKILL_PR, '.codebuddy', 'skills', SKILL_NAME)}/"
            )
        print("│  Tip: restart CodeBuddy")
        print("└────────────────────────────────────────")
    else:
        log_error("Verification failed")
        sys.exit(1)


def run_uninstall_codebuddy():
    sn = "qpanda3-runtime"
    skn = "qpanda3-programming"
    log_info("Uninstalling CodeBuddy MCP configuration")
    for p, lb in [
        (os.path.expanduser("~/.codebuddy/.mcp.json"), "CodeBuddy"),
        (os.path.expanduser("~/.codebuddy/mcp.json"), "CodeBuddy"),
        (os.path.expanduser("~/.codebuddy.json"), "CodeBuddy"),
        (os.path.join(SCRIPT_DIR, ".mcp.json"), "CodeBuddy project"),
        (os.path.join(SCRIPT_DIR, "mcp.json"), "CodeBuddy project"),
    ]:
        r = uninstall_json_entry(p, "mcpServers", sn)
        if r == "REMOVED":
            log_ok(f"Removed {sn} from {lb}")
    print()
    log_info("Uninstalling Skill")
    for b, lb in [
        (os.path.expanduser("~/.codebuddy/skills"), "user"),
        (os.path.join(SCRIPT_DIR, ".codebuddy", "skills"), "project"),
    ]:
        t = os.path.join(b, skn)
        if uninstall_skill_dir(t):
            log_ok(f"Removed: {lb}")
        else:
            log_warn(f"{lb} not found")
    print()
    log_ok("Uninstall complete! Restart CodeBuddy.")


# ---------- Cline ----------


def cline_mcp_merge():
    os.makedirs(CLN_CONFIG_DIR, exist_ok=True)
    if os.path.isfile(CLN_CONFIG_FILE):
        with open(CLN_CONFIG_FILE, encoding="utf-8") as f:
            ct = f.read()
        ex = stdjson.loads(ct) if ct.strip() else {}
        nd = stdjson.loads(CLN_NORMALIZED_JSON)
        if "mcpServers" not in ex:
            ex["mcpServers"] = {}
        for n, e in nd["mcpServers"].items():
            ex["mcpServers"][n] = e
        merged = stdjson.dumps(ex, indent=2, ensure_ascii=False)
    else:
        merged = CLN_NORMALIZED_JSON
    with open(CLN_CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(merged)
        log_ok(f"Written: {CLN_CONFIG_FILE}")


def run_prepare_cline(mcp_cfg: dict, skill_url: str, scope: str):
    global CLN_CONFIG_DIR, CLN_CONFIG_FILE, CLN_NORMALIZED_JSON
    log_info("Configuring Cline MCP")
    CLN_CONFIG_DIR = os.path.expanduser("~/.cline/data/settings")
    CLN_CONFIG_FILE = os.path.join(CLN_CONFIG_DIR, "cline_mcp_settings.json")
    CLN_NORMALIZED_JSON = stdjson.dumps(
        wrap_mcp_entry("cline", mcp_cfg), indent=2, ensure_ascii=False
    )
    cline_mcp_merge()

    log_info("Installing QPanda3 Skill")
    SKILL_NAME = shared_download_zip(skill_url)
    target = os.path.expanduser(f"~/.cline/skills/{SKILL_NAME}")
    shared_extract_and_install(SKILL_ZIP_PATH, target, f"user {target}")

    t = os.path.expanduser(f"~/.cline/skills/{SKILL_NAME}/SKILL.md")
    if os.path.isfile(t):
        log_ok(f"Skill {SKILL_NAME} installed!")
        print("┌────────────────────────────────────────")
        print(f"│  Skill: {SKILL_NAME}")
        print(f"│  user: {os.path.expanduser(f'~/.cline/skills/{SKILL_NAME}/')}")
        print("│  Tip: restart Cline")
        print("└────────────────────────────────────────")
    else:
        log_error("Verification failed")
        sys.exit(1)


def run_uninstall_cline():
    sn = "qpanda3-runtime"
    skn = "qpanda3-programming"
    log_info("Uninstalling Cline MCP configuration")
    uninstall_print_result(
        "Cline",
        uninstall_json_entry(
            os.path.expanduser("~/.cline/data/settings/cline_mcp_settings.json"),
            "mcpServers",
            sn,
        ),
    )
    print()
    log_info("Uninstalling Skill")
    t = os.path.expanduser(f"~/.cline/skills/{skn}")
    if uninstall_skill_dir(t):
        log_ok(f"Removed: {t}")
    else:
        log_warn(f"{t} not found")
    print()
    log_ok("Uninstall complete! Restart Cline.")


# ---------- Codewhale ----------


def codewhale_mcp_merge():
    os.makedirs(CW_CONFIG_DIR, exist_ok=True)
    nd = stdjson.loads(CW_NORMALIZED_JSON)
    servers = nd.get("servers", {})
    existing = {}
    if os.path.isfile(CW_CONFIG_FILE):
        with open(CW_CONFIG_FILE, encoding="utf-8") as f:
            ct = f.read()
        if ct.strip():
            existing = stdjson.loads(ct)
    ms = existing.get("servers", {})
    for n, e in servers.items():
        ms[n] = e
    existing["servers"] = ms
    with open(CW_CONFIG_FILE, "w", encoding="utf-8") as f:
        stdjson.dump(existing, f, indent=2, ensure_ascii=False)
        f.write("\n")
        log_ok(f"Written: {CW_CONFIG_FILE}")


def codewhale_ensure_priority():
    """Ensure '预加载技能库和mcp工具列表' exists in constitution.json priorities."""
    cw_dir = os.path.expanduser("~/.codewhale")
    constitution_file = os.path.join(cw_dir, "constitution.json")
    if not os.path.isfile(constitution_file):
        log_error(
            f"constitution.json not found at {constitution_file}, "
            "required for Codewhale configuration"
        )
        sys.exit(1)
    try:
        with open(constitution_file, encoding="utf-8") as f:
            data = stdjson.load(f)
        priorities = data.get("priorities", [])
        target = "预加载技能库和mcp工具列表"
        if target in priorities:
            log_ok("Priority '预加载技能库和mcp工具列表' already exists")
            return
        priorities.append(target)
        data["priorities"] = priorities
        with open(constitution_file, "w", encoding="utf-8") as f:
            stdjson.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        log_ok("Added priority '预加载技能库和mcp工具列表' to constitution.json")
    except Exception as e:
        log_error(f"Failed to update constitution.json: {e}")
        sys.exit(1)


def run_prepare_codewhale(mcp_cfg: dict, skill_url: str, scope: str):
    global CW_CONFIG_DIR, CW_CONFIG_FILE, CW_NORMALIZED_JSON
    log_info("Configuring Codewhale MCP")
    CW_CONFIG_DIR = os.path.expanduser("~/.codewhale")
    CW_CONFIG_FILE = os.path.join(CW_CONFIG_DIR, "mcp.json")
    CW_NORMALIZED_JSON = stdjson.dumps(
        wrap_mcp_entry("codewhale", mcp_cfg), indent=2, ensure_ascii=False
    )
    codewhale_mcp_merge()
    codewhale_ensure_priority()

    log_info("Installing QPanda3 Skill")
    SKILL_NAME = shared_download_zip(skill_url)
    target = os.path.expanduser(f"~/.codewhale/skills/{SKILL_NAME}")
    shared_extract_and_install(SKILL_ZIP_PATH, target, f"user {target}")

    t = os.path.expanduser(f"~/.codewhale/skills/{SKILL_NAME}/SKILL.md")
    if os.path.isfile(t):
        log_ok(f"Skill {SKILL_NAME} installed!")
        print("┌────────────────────────────────────────")
        print(f"│  Skill: {SKILL_NAME}")
        print(f"│  user: {os.path.expanduser(f'~/.codewhale/skills/{SKILL_NAME}/')}")
        print("│  Tip: restart Codewhale")
        print("└────────────────────────────────────────")
    else:
        log_error("Verification failed")
        sys.exit(1)


def run_uninstall_codewhale():
    sn = "qpanda3-runtime"
    skn = "qpanda3-programming"
    log_info("Uninstalling Codewhale MCP configuration")
    uninstall_print_result(
        "Codewhale",
        uninstall_json_entry(
            os.path.expanduser("~/.codewhale/mcp.json"),
            "servers",
            sn,
        ),
    )
    print()
    log_info("Uninstalling Skill")
    t = os.path.expanduser(f"~/.codewhale/skills/{skn}")
    if uninstall_skill_dir(t):
        log_ok(f"Removed: {t}")
    else:
        log_warn(f"{t} not found")
    print()
    log_ok("Uninstall complete! Restart Codewhale.")


# ---------- ZCode Agent ----------


def zcode_mcp_merge():
    os.makedirs(ZC_CONFIG_DIR, exist_ok=True)
    existing = {}
    if os.path.isfile(ZC_CONFIG_FILE):
        with open(ZC_CONFIG_FILE, encoding="utf-8") as f:
            ct = f.read()
        if ct.strip():
            existing = stdjson.loads(ct)
    nd = stdjson.loads(ZC_NORMALIZED_JSON)
    mcp_cfg = nd.get("mcp", {})
    mcp = existing.get("mcp", {})
    servers = mcp.get("servers", {})
    for n, e in mcp_cfg.get("servers", {}).items():
        servers[n] = e
    mcp["servers"] = servers
    existing["mcp"] = mcp
    with open(ZC_CONFIG_FILE, "w", encoding="utf-8") as f:
        stdjson.dump(existing, f, indent=2, ensure_ascii=False)
        f.write("\n")
        log_ok(f"Written: {ZC_CONFIG_FILE}")


def run_prepare_zcode(mcp_cfg: dict, skill_url: str, scope: str):
    global ZC_CONFIG_DIR, ZC_CONFIG_FILE, ZC_NORMALIZED_JSON
    log_info("Configuring ZCode Agent MCP")
    ZC_CONFIG_DIR = os.path.expanduser("~/.zcode/cli")
    ZC_CONFIG_FILE = os.path.join(ZC_CONFIG_DIR, "config.json")
    ZC_NORMALIZED_JSON = stdjson.dumps(
        wrap_mcp_entry("zcode", mcp_cfg), indent=2, ensure_ascii=False
    )
    zcode_mcp_merge()

    log_info("Installing QPanda3 Skill")
    SKILL_NAME = shared_download_zip(skill_url)
    target = os.path.expanduser(f"~/.zcode/skills/{SKILL_NAME}")
    shared_extract_and_install(SKILL_ZIP_PATH, target, f"user {target}")

    t = os.path.expanduser(f"~/.zcode/skills/{SKILL_NAME}/SKILL.md")
    if os.path.isfile(t):
        log_ok(f"Skill {SKILL_NAME} installed!")
        print("┌────────────────────────────────────────")
        print(f"│  Skill: {SKILL_NAME}")
        print(f"│  user: {os.path.expanduser(f'~/.zcode/skills/{SKILL_NAME}/')}")
        print("│  Tip: restart ZCode Agent")
        print("└────────────────────────────────────────")
    else:
        log_error("Verification failed")
        sys.exit(1)


def run_uninstall_zcode():
    sn = "qpanda3-runtime"
    skn = "qpanda3-programming"
    log_info("Uninstalling ZCode Agent MCP configuration")
    r = uninstall_nested_json_entry(
        os.path.expanduser("~/.zcode/cli/config.json"), ["mcp", "servers"], sn
    )
    uninstall_print_result("ZCode global", r)
    print()
    log_info("Uninstalling Skill")
    t = os.path.expanduser(f"~/.zcode/skills/{skn}")
    if uninstall_skill_dir(t):
        log_ok(f"Removed: {t}")
    else:
        log_warn(f"{t} not found")
    print()
    log_ok("Uninstall complete! Restart ZCode Agent.")


def uninstall_nested_json_entry(fp, keys: list, server_name: str):
    """Remove server_name from a nested JSON key path like data['mcp']['servers']."""
    if not os.path.isfile(fp):
        return None
    try:
        with open(fp, encoding="utf-8") as f:
            ct = f.read()
        if not ct.strip():
            return None
        data = stdjson.loads(ct)
        d = data
        for k in keys[:-1]:
            d = d.get(k, {})
        parent_key = keys[-1]
        se = d.get(parent_key, {})
        if server_name not in se:
            return "NOT_FOUND"
        del se[server_name]
        d[parent_key] = se
        with open(fp, "w", encoding="utf-8") as f:
            stdjson.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return "REMOVED"
    except Exception as e:
        return f"ERROR: {e}"


# ══════════════════════ Main Entry ══════════════════════


def cmd_list():
    print("Supported AI coding tools:")
    for key, info in SUPPORTED_TOOLS.items():
        print(f"  {key:12s} {info['name']}")


def cmd_help():
    print("QPanda3 AI Coding Tools Setup")
    print("=" * 40)
    print("This tool configures the QPanda3 Runtime MCP server and")
    print("installs the QPanda3 programming skill for AI coding tools.")
    print()
    print("Supported tools:")
    for key, info in SUPPORTED_TOOLS.items():
        print(f"  {key:12s} {info['name']}")
    print()
    print("Usage: prepare_qpanda3_ai_kits.py <command> [options]")
    print()
    print("commands:")
    print("  list                       List supported AI coding tools")
    print("  prepare  [tool]  -k KEY [-p]   Prepare environment")
    print("  uninstall [tool]           Uninstall environment")
    print("  help                       Show this help")
    print()
    print("prepare options:")
    print("  tool      AI coding tool name (from list), auto-detect if omitted")
    print("  -k KEY    API key string")
    print("  -p, --project  Project-level config (script dir as project root)")
    print()
    print("uninstall:")
    print("  tool      AI coding tool name, uninstall all if omitted")
    print()
    print("Examples:")
    print("  uv run prepare_qpanda3_ai_kits.py list")
    print("  uv run prepare_qpanda3_ai_kits.py prepare opencode -k mykey")
    print("  uv run prepare_qpanda3_ai_kits.py prepare -k mykey")
    print("  uv run prepare_qpanda3_ai_kits.py prepare codex -k mykey -p")
    print("  uv run prepare_qpanda3_ai_kits.py uninstall claude")
    print("  uv run prepare_qpanda3_ai_kits.py uninstall")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        prog="prepare_qpanda3_ai_kits.py",
        description="Prepare QPanda3 dev environment for AI coding tools",
        add_help=False,
    )
    parser.add_argument(
        "command", nargs="?", help="subcommand: list, prepare, uninstall, help"
    )
    parser.add_argument("tool", nargs="?", help="AI coding tool name")
    parser.add_argument("-k", "--apikey", help="API key or config file path")
    parser.add_argument(
        "-p", "--project", action="store_true", help="project-level config"
    )
    args = parser.parse_args()

    cmd = args.command or "help"

    if cmd == "list":
        cmd_list()
    elif cmd == "help":
        cmd_help()
    elif cmd == "prepare":
        if not args.apikey:
            log_error("-k/--apikey is required")
            sys.exit(1)
        mcp_cfg, skill_url = resolve_k_param(args.apikey)
        if mcp_cfg is None:
            mcp_cfg = generate_mcp_entry(args.apikey, DEFAULT_MCP_URL)
        scope = "project" if args.project else "user"

        tools = [args.tool] if args.tool else detect_installed_tools()
        if not tools:
            log_error("No tool specified and no installed tools detected")
            sys.exit(1)

        for t in tools:
            if t not in SUPPORTED_TOOLS:
                log_warn(f"Unknown tool: {t}, skipped")
                continue
            if not check_tool_usable(t, scope):
                continue
            prep_fn = {
                "opencode": run_prepare_opencode,
                "claude": run_prepare_claude,
                "codex": run_prepare_codex,
                "gemini": run_prepare_gemini,
                "codebuddy": run_prepare_codebuddy,
                "cline": run_prepare_cline,
                "codewhale": run_prepare_codewhale,
                "zcode": run_prepare_zcode,
            }.get(t)
            if prep_fn:
                print(f"\n{'=' * 44}")
                print(f"  {SUPPORTED_TOOLS[t]['name']} Environment Setup")
                print(f"{'=' * 44}")
                prep_fn(mcp_cfg, skill_url, scope)
    elif cmd == "uninstall":
        tools = [args.tool] if args.tool else list(SUPPORTED_TOOLS.keys())
        for t in tools:
            if t not in SUPPORTED_TOOLS:
                log_warn(f"Unknown tool: {t}, skipped")
                continue
            uninstall_fn = {
                "opencode": run_uninstall_opencode,
                "claude": run_uninstall_claude,
                "codex": run_uninstall_codex,
                "gemini": run_uninstall_gemini,
                "codebuddy": run_uninstall_codebuddy,
                "cline": run_uninstall_cline,
                "codewhale": run_uninstall_codewhale,
                "zcode": run_uninstall_zcode,
            }.get(t)
            if uninstall_fn:
                print(f"\n{'=' * 44}")
                print(f"  Uninstalling {SUPPORTED_TOOLS[t]['name']}")
                print(f"{'=' * 44}")
                uninstall_fn()
    else:
        log_error(f"Unknown command: {cmd}")
        cmd_help()
        sys.exit(1)


if __name__ == "__main__":
    SKILL_NAME = ""
    SKILL_ZIP_URL = ""
    SKILL_ZIP_PATH = ""
    OC_NORMALIZED_JSON = ""
    OC_CONFIG_DIR = ""
    OC_CONFIG_FILE = ""
    OC_SKILL_IG = False
    OC_SKILL_IP = False
    OC_SKILL_PR = ""
    CL_NORMALIZED_JSON = ""
    CL_CONFIG_DIR = ""
    CL_CONFIG_FILE = ""
    CL_SKILL_IG = False
    CL_SKILL_IP = False
    CL_SKILL_PR = ""
    CDX_NORMALIZED_JSON = ""
    CDX_CONFIG_DIR = ""
    CDX_CONFIG_FILE = ""
    CDX_SKILL_IG = False
    CDX_SKILL_IP = False
    CDX_SKILL_PR = ""
    GM_NORMALIZED_JSON = ""
    GM_CONFIG_DIR = ""
    GM_CONFIG_FILE = ""
    GM_SKILL_IG = False
    GM_SKILL_IP = False
    GM_SKILL_PR = ""
    CB_NORMALIZED_JSON = ""
    CB_CONFIG_DIR = ""
    CB_CONFIG_FILE = ""
    CB_SKILL_IG = False
    CB_SKILL_IP = False
    CB_SKILL_PR = ""
    CLN_NORMALIZED_JSON = ""
    CLN_CONFIG_DIR = ""
    CLN_CONFIG_FILE = ""
    CW_NORMALIZED_JSON = ""
    CW_CONFIG_DIR = ""
    CW_CONFIG_FILE = ""
    ZC_NORMALIZED_JSON = ""
    ZC_CONFIG_DIR = ""
    ZC_CONFIG_FILE = ""
    ZC_SKILL_IG = False
    ZC_SKILL_IP = False
    ZC_SKILL_PR = ""
    main()
