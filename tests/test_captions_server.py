"""The small-model caption server: five strict tools over the live caption helpers.

Offline: the compound server's timeline_ai is patched out, as is the Node codec.
"""
import asyncio
import json
import re
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src import captions_server as cs
from src.utils import subtitle_live as live

ROOT = Path(__file__).resolve().parents[1]
CAPTIONS = {"success": True, "fps": 30, "captions": [
    {"id": "100", "text": "hello wrold", "start": 100, "end": 130,
     "words": [{"text": "hello", "start": 100, "end": 115}, {"text": "wrold", "start": 115, "end": 130}]},
    {"id": "140", "text": "ok", "start": 140, "end": 150, "words": [{"text": "ok", "start": 140, "end": 150}]},
]}


class RetimeWordsTest(unittest.TestCase):
    def test_words_tile_the_caption_by_letter_count(self):
        words = cs.retime_words("Hi there", 100, 130)
        self.assertEqual([w["text"] for w in words], ["Hi", "there"])
        self.assertEqual(words[0]["start"], 100)
        self.assertEqual(words[-1]["end"], 130)
        self.assertEqual(words[0]["end"], words[1]["start"])
        self.assertAlmostEqual(words[0]["end"], 100 + 30 * 2 / 7)

    def test_empty_text_is_refused(self):
        with self.assertRaises(ValueError):
            cs.retime_words("   ", 0, 10)


class ToolsTest(unittest.TestCase):
    def test_get_returns_only_id_and_text(self):
        with patch.object(cs, "timeline_ai", return_value=CAPTIONS):
            self.assertEqual(cs.captions_get(track=2),
                             {"success": True, "captions": [{"id": "100", "text": "hello wrold"}, {"id": "140", "text": "ok"}]})

    def test_fix_text_retimes_words_and_keeps_caption_bounds(self):
        with patch.object(cs, "timeline_ai", side_effect=[CAPTIONS, {"success": True}]) as ai:
            result = cs.captions_fix_text([cs.TextEdit(id="100", text="hello world")], track=2)
        self.assertTrue(result["success"])
        action, params = ai.call_args_list[1].args
        self.assertEqual(action, "write_captions_live")
        self.assertEqual(params["track"], 2)
        replaced = params["replace"][0]
        self.assertEqual((replaced["id"], replaced["text"], replaced["start"], replaced["end"]), ("100", "hello world", 100, 130))
        self.assertEqual([w["text"] for w in replaced["words"]], ["hello", "world"])
        self.assertEqual(replaced["words"][-1]["end"], 130)

    def test_fix_text_refuses_unknown_ids_before_writing(self):
        with patch.object(cs, "timeline_ai", side_effect=[CAPTIONS, {"success": True}]) as ai:
            result = cs.captions_fix_text([cs.TextEdit(id="999", text="x")])
        self.assertFalse(result["success"])
        self.assertIn("999", result["error"])
        self.assertEqual(ai.call_count, 1)

    def test_style_applies_a_recipe_on_the_bundled_preset(self):
        with patch.object(cs, "timeline_ai", return_value={"success": True}) as ai:
            cs.captions_style("social_bold", track=1)
        action, params = ai.call_args.args
        self.assertEqual(action, "set_subtitle_preset")
        self.assertEqual(params["preset"], "Word Highlight")
        self.assertEqual(params["inputs"], cs.RECIPES["social_bold"])

    def test_recipes_only_use_inputs_the_codec_maps(self):
        codec = (ROOT / "resolve-advanced/vendor/drp-format/subtitle-preset.js").read_text(encoding="utf-8")
        mapped = set(re.findall(r"\b(\w+): '[A-Za-z0-9]+'", codec))
        for name, inputs in cs.RECIPES.items():
            self.assertFalse(set(inputs) - mapped, f"{name} uses inputs the codec does not map")

    def test_check_runs_the_codec_check(self):
        with patch.object(cs, "_check", return_value={"success": True, "ok": True}) as check:
            cs.captions_check(track=1, max_characters=20)
        self.assertEqual(check.call_args.args[0], {"track": 1, "maxCharacters": 20})

    def test_tool_surface_is_small_and_strict(self):
        tools = asyncio.run(cs.mcp.list_tools())
        self.assertEqual(sorted(t.name for t in tools),
                         ["captions_check", "captions_fix_text", "captions_generate", "captions_get", "captions_style"])
        budget = sum(len(t.description or "") + len(json.dumps(t.inputSchema)) for t in tools)
        self.assertLess(budget, 4000, f"tool surface is {budget} chars; keep it small for local models")
        style = next(t for t in tools if t.name == "captions_style")
        self.assertEqual(set(style.inputSchema["properties"]["recipe"]["enum"]), set(cs.RECIPES))
        fix = next(t for t in tools if t.name == "captions_fix_text")
        edit = fix.inputSchema["$defs"]["TextEdit"]
        self.assertEqual(sorted(edit["required"]), ["id", "text"])
        self.assertFalse(edit.get("additionalProperties", True))


class CheckCaptionsLiveTest(unittest.TestCase):
    def test_exports_then_runs_the_check_operation(self):
        timeline = Mock()
        timeline.GetTrackCount.return_value = 1
        timeline.GetName.return_value = "Cut"
        timeline.Export.return_value = True
        report = {"ok": False, "issues": [{"id": "100", "code": "overflow_length"}]}
        with patch.object(live.shutil, "which", return_value="node"), \
             patch.object(live, "subtitle_codec_gap", return_value=None), \
             patch.object(live.subprocess, "run", return_value=Mock(returncode=0, stdout=json.dumps(report))) as run:
            result = live.check_captions_live(Mock(EXPORT_DRT=1), timeline, {"track": 1, "maxCharacters": 20},
                                              lambda path: path)
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual((request["operation"], request["track"], request["maxCharacters"]), ("check_captions", 1, 20))
        self.assertEqual(result["issues"], report["issues"])
        self.assertEqual(result["timeline"], "Cut")


if __name__ == "__main__":
    unittest.main()
