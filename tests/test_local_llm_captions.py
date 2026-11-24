"""The local-model launcher: Ollama checks, the ollmcp servers file, and starting the chat."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("local_llm_captions", ROOT / "scripts" / "local_llm_captions.py")
lc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lc)

PATHS = (r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting",
         r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")


class OllamaUrlTest(unittest.TestCase):
    def test_default_and_common_ollama_host_forms(self):
        for value, expected in [("", "http://127.0.0.1:11434"), ("0.0.0.0:11434", "http://127.0.0.1:11434"),
                                ("http://192.168.1.5:11434/", "http://192.168.1.5:11434")]:
            with patch.dict(os.environ, {"OLLAMA_HOST": value}):
                self.assertEqual(lc.ollama_url(), expected, value)


class ConfigTest(unittest.TestCase):
    def test_servers_file_runs_the_captions_server_on_this_python_with_resolve_paths(self):
        entry = lc.servers_config(lc._install(), PATHS)["mcpServers"]["resolve-captions"]
        self.assertEqual(entry["command"], sys.executable)
        self.assertEqual(Path(entry["args"][0]), ROOT / "src" / "captions_server.py")
        self.assertEqual((entry["env"]["RESOLVE_SCRIPT_API"], entry["env"]["RESOLVE_SCRIPT_LIB"]), PATHS)


class ProblemsTest(unittest.TestCase):
    def check(self, paths=PATHS, tools=True, node="node"):
        with patch.object(lc.shutil, "which", return_value=node), \
             patch.object(lc, "subtitle_codec_gap", return_value=None), \
             patch.object(lc, "supports_tools", side_effect=[tools] if isinstance(tools, bool) else tools):
            return lc.problems(paths, "my-model")

    def test_a_ready_setup_has_no_problems(self):
        self.assertEqual(self.check(), [])

    def test_each_missing_piece_is_named(self):
        self.assertIn("scripting library", self.check(paths=(None, None))[0])
        self.assertIn("Node.js", self.check(node=None)[0])
        self.assertIn("cannot call tools", self.check(tools=False)[0])
        missing = urllib.error.HTTPError("http://x/api/show", 404, "not found", {}, None)
        self.assertIn("ollama pull my-model", self.check(tools=missing)[0])
        self.assertIn("not answering", self.check(tools=urllib.error.URLError("refused"))[0])


class MainTest(unittest.TestCase):
    def run_main(self, argv, **patches):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(out), \
             patch.object(lc, "CONFIG", Path(tmp) / "servers.json"), \
             patch.object(lc, "problems", return_value=patches.get("problems", [])), \
             patch.object(lc, "_ollmcp", return_value=patches.get("ollmcp", "ollmcp")), \
             patch.object(lc.subprocess, "call", return_value=0) as call:
            code = lc.main(argv)
            written = json.loads((Path(tmp) / "servers.json").read_text(encoding="utf-8")) if argv else None
        return code, out.getvalue(), call, written

    def test_without_a_model_it_lists_models_that_can_call_tools(self):
        with patch.object(lc, "tool_models", return_value=["qwen3:8b"]):
            code, out, call, _ = self.run_main([])
        self.assertEqual(code, 0)
        self.assertIn("qwen3:8b", out)
        call.assert_not_called()

    def test_check_writes_the_servers_file_and_prints_the_command(self):
        code, out, call, written = self.run_main(["-m", "qwen3:8b", "--check"])
        self.assertEqual(code, 0)
        self.assertIn("resolve-captions", written["mcpServers"])
        self.assertIn("--servers-json", out)
        call.assert_not_called()

    def test_problems_stop_before_the_chat(self):
        code, out, call, _ = self.run_main(["-m", "qwen3:8b"], problems=["Node.js is not on PATH"])
        self.assertEqual(code, 1)
        self.assertIn("Node.js", out)
        call.assert_not_called()

    def test_a_ready_setup_starts_ollmcp_with_the_servers_file_and_model(self):
        code, _, call, _ = self.run_main(["-m", "qwen3:8b"], ollmcp="C:/venv/Scripts/ollmcp.exe")
        command = call.call_args.args[0]
        self.assertEqual(command[:2], ["C:/venv/Scripts/ollmcp.exe", "--servers-json"])
        self.assertEqual(command[command.index("--model") + 1], "qwen3:8b")

    def test_missing_ollmcp_prints_how_to_install_it(self):
        code, out, call, _ = self.run_main(["-m", "qwen3:8b"], ollmcp=None)
        self.assertEqual(code, 0)
        self.assertIn("pip install ollmcp", out)
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
