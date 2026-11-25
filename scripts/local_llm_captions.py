#!/usr/bin/env python3
"""Check the local setup and start the caption tools with a local Ollama model.

Built for Windows first; macOS and Linux work the same way. Run it with the
project's virtual environment so the MCP server runs on that Python:

    venv\\Scripts\\python scripts\\local_llm_captions.py                    list pulled models that can call tools
    venv\\Scripts\\python scripts\\local_llm_captions.py -m <model>         check the setup, then start ollmcp
    venv\\Scripts\\python scripts\\local_llm_captions.py -m <model> --check check only

It writes logs/ollmcp-servers.json with the same server entry install.py writes
for other clients (Resolve paths, and PYTHONHOME on Windows).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "logs" / "ollmcp-servers.json"  # logs/ is git-ignored
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.subtitle_live import subtitle_codec_gap  # noqa: E402

OLLAMA_DOWN = "Ollama is not answering at {}. Start the Ollama app, or set OLLAMA_HOST."


def ollama_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "").strip() or "127.0.0.1:11434"
    if "://" not in host:
        host = f"http://{host}"
    # 0.0.0.0 is a listen address (set on Windows to share Ollama on the network); connect locally.
    return host.replace("//0.0.0.0", "//127.0.0.1").rstrip("/")


def _ollama(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(ollama_url() + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def supports_tools(model: str) -> bool:
    return "tools" in _ollama("/api/show", {"model": model}).get("capabilities", [])


def tool_models() -> list[str]:
    """Pulled models whose Ollama capabilities include tool calling."""
    return [m["name"] for m in _ollama("/api/tags").get("models", []) if supports_tools(m["name"])]


def _install():
    spec = importlib.util.spec_from_file_location("resolve_install", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def servers_config(install, paths: tuple) -> dict:
    """ollmcp's servers file: the captions server on this Python, with install.py's env block."""
    entry = install.build_server_entry(sys.executable, ROOT / "src" / "captions_server.py", *paths)
    return {"mcpServers": {"resolve-captions": entry}}


def problems(paths: tuple, model: str) -> list[str]:
    found = []
    if not paths[1]:
        found.append("DaVinci Resolve's scripting library was not found. Install Resolve Studio, "
                     "or set RESOLVE_SCRIPT_LIB to fusionscript.dll.")
    if not shutil.which("node"):
        found.append("Node.js is not on PATH; the caption codec needs it (nodejs.org, LTS).")
    elif subtitle_codec_gap():
        found.append(subtitle_codec_gap())
    try:
        if not supports_tools(model):
            found.append(f"'{model}' cannot call tools in Ollama. Run this script without -m to list models that can.")
    except urllib.error.HTTPError:
        found.append(f"Ollama has no model '{model}'. Pull it first: ollama pull {model}")
    except (urllib.error.URLError, OSError):
        found.append(OLLAMA_DOWN.format(ollama_url()))
    return found


def _ollmcp() -> str | None:
    """ollmcp on PATH, or next to this Python (installed into the venv without activating it)."""
    beside = Path(sys.executable).parent / ("ollmcp.exe" if os.name == "nt" else "ollmcp")
    return shutil.which("ollmcp") or (str(beside) if beside.is_file() else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the setup and start the caption tools with a local Ollama model.")
    parser.add_argument("-m", "--model", help="Ollama model that can call tools; omit to list them")
    parser.add_argument("--check", action="store_true", help="check and write the config, but do not start the chat")
    args = parser.parse_args(argv)

    if not args.model:
        try:
            models = tool_models()
        except (urllib.error.URLError, OSError):
            print(OLLAMA_DOWN.format(ollama_url()))
            return 2
        if not models:
            print("No pulled model can call tools. Pull one that ollama.com/search?c=tools lists, then run this again.")
            return 1
        print("\n".join(["Pulled models that can call tools:", *(f"  {m}" for m in models)]))
        return 0

    install = _install()
    paths = install.find_resolve_paths()
    CONFIG.parent.mkdir(exist_ok=True)
    CONFIG.write_text(json.dumps(servers_config(install, paths), indent=2), encoding="utf-8")
    print(f"Wrote {CONFIG}")
    found = problems(paths, args.model)
    for problem in found:
        print(f"  - {problem}")
    if found:
        return 1

    client = _ollmcp()
    command = [client or "ollmcp", "--servers-json", str(CONFIG), "--model", args.model, "--host", ollama_url()]
    if args.check or not client:
        if not client:
            print(f'ollmcp is not installed. Install it into this environment:\n  "{sys.executable}" -m pip install ollmcp')
        print("Start the chat with:\n  " + (subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)))
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    sys.exit(main())
