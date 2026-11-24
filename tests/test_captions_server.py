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
from src.server import _err
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


SOCIAL = {"id": "108086", "text": "Oh, that's such a cute", "start": 108086, "end": 108143, "words": [
    {"text": "Oh,", "start": 108086, "end": 108106}, {"text": "that's", "start": 108106, "end": 108125},
    {"text": "such", "start": 108125, "end": 108131}, {"text": "a", "start": 108131, "end": 108137},
    {"text": "cute", "start": 108137, "end": 108143}]}


def assert_valid_cards(test, caption, cards):
    test.assertEqual((cards[0]["start"], cards[-1]["end"]), (caption["start"], caption["end"]))
    for card, following in zip(cards, cards[1:]):
        test.assertEqual(card["end"], following["start"], "cards tile the caption")
    for card in cards:
        test.assertIsInstance(card["start"], int)
        test.assertEqual(card["text"], " ".join(w["text"] for w in card["words"]))
        test.assertEqual((card["words"][0]["start"], card["words"][-1]["end"]), (card["start"], card["end"]))
        for word, following in zip(card["words"], card["words"][1:]):
            test.assertEqual(word["end"], following["start"], "words tile the card")
        test.assertTrue(all(w["end"] > w["start"] for w in card["words"]))


class SplitCaptionTest(unittest.TestCase):
    def test_splits_at_resolve_word_timings(self):
        cards = cs.split_caption(SOCIAL, 3)
        self.assertEqual([(c["text"], c["start"], c["end"]) for c in cards],
                         [("Oh, that's such", 108086, 108131), ("a cute", 108131, 108143)])
        self.assertEqual(cards[0]["words"], SOCIAL["words"][:3], "measured word timings are kept")
        assert_valid_cards(self, SOCIAL, cards)

    def test_a_card_too_short_to_read_merges_into_its_neighbour(self):
        caption = {"id": "0", "text": "a b c d", "start": 0, "end": 31, "words": [
            {"text": "a", "start": 0, "end": 10}, {"text": "b", "start": 10, "end": 20},
            {"text": "c", "start": 20, "end": 30}, {"text": "d", "start": 30, "end": 31}]}
        cards = cs.split_caption(caption, 3)
        self.assertEqual([c["text"] for c in cards], ["a b c d"])
        first = {"id": "0", "text": "a b c d", "start": 0, "end": 30, "words": [
            {"text": "a", "start": 0, "end": 1}, {"text": "b", "start": 1, "end": 10},
            {"text": "c", "start": 10, "end": 20}, {"text": "d", "start": 20, "end": 30}]}
        cards = cs.split_caption(first, 1)
        self.assertEqual([c["text"] for c in cards], ["a b", "c", "d"], "the first card joins the next")
        assert_valid_cards(self, first, cards)

    def test_sub_frame_word_timings_still_give_valid_cards(self):
        caption = {"id": "0", "text": "one two three four", "start": 0, "end": 20, "words": [
            {"text": "one", "start": 0, "end": 4.6}, {"text": "two", "start": 4.6, "end": 4.8},
            {"text": "three", "start": 4.8, "end": 12.2}, {"text": "four", "start": 12.2, "end": 20}]}
        for max_words in (1, 2, 3):
            assert_valid_cards(self, caption, cs.split_caption(caption, max_words))


class ToolsTest(unittest.TestCase):
    def test_split_replaces_long_captions_and_adds_the_extra_cards(self):
        short = {"id": "108143", "text": "cat", "start": 108143, "end": 108160,
                 "words": [{"text": "cat", "start": 108143, "end": 108160}]}
        listed = {"success": True, "fps": 30, "captions": [SOCIAL, short]}
        with patch.object(cs, "timeline_ai", side_effect=[listed, {"success": True}]) as ai:
            cs.captions_split(track=2, max_words=3)
        action, params = ai.call_args.args
        self.assertEqual((action, params["track"]), ("write_captions_live", 2))
        self.assertEqual([(c["id"], c["end"]) for c in params["replace"]], [("108086", 108131)])
        self.assertEqual([(c["text"], c["start"]) for c in params["add"]], [("a cute", 108131)])

    def test_captions_without_word_timings_are_skipped_not_fatal(self):
        broken = {"id": "200", "text": "a b c d", "start": 200, "end": 240, "decodeIssues": ["word_count_mismatch"],
                  "words": [{"text": t, "start": None, "end": None} for t in "abcd"]}
        listed = {"success": True, "captions": [SOCIAL, broken]}
        with patch.object(cs, "timeline_ai", side_effect=[listed, {"success": True}]) as ai:
            result = cs.captions_split(max_words=3)
        self.assertEqual([c["id"] for c in ai.call_args.args[1]["replace"]], ["108086"])
        self.assertEqual(result["skipped_without_word_timings"], ["200"])

    def test_gaps_between_words_are_closed_so_the_codec_accepts_the_cards(self):
        gappy = {"id": "0", "text": "a b c d", "start": 0, "end": 40, "words": [
            {"text": "a", "start": 0, "end": 8}, {"text": "b", "start": 10, "end": 20},
            {"text": "c", "start": 20, "end": 28}, {"text": "d", "start": 31, "end": 40}]}
        assert_valid_cards(self, gappy, cs.split_caption(gappy, 2))

    def test_split_with_nothing_to_split_writes_nothing(self):
        with patch.object(cs, "timeline_ai", return_value={"success": True, "captions": [SOCIAL]}) as ai:
            result = cs.captions_split(max_words=5)
        self.assertEqual((result["success"], result["changed"]), (True, 0))
        self.assertEqual(ai.call_count, 1)

    def test_get_returns_only_id_and_text(self):
        with patch.object(cs, "timeline_ai", return_value=CAPTIONS) as ai:
            self.assertEqual(cs.captions_get(track=2),
                             {"success": True, "captions": [{"id": "100", "text": "hello wrold"}, {"id": "140", "text": "ok"}]})
        # The plain dict regardless of RESOLVE_MCP_RESULT_ENVELOPE=pure in the environment.
        self.assertEqual(ai.call_args.args[1]["envelope"], "legacy")

    def test_generate_returns_the_new_track_index(self):
        created = {"success": True, "verified": True, "subtitle_tracks_before": 1, "subtitle_tracks_after": 2}
        with patch.object(cs, "timeline_ai", return_value=created):
            self.assertEqual(cs.captions_generate()["track"], 2)
        with patch.object(cs, "timeline_ai", return_value={"success": False, "verified": False}):
            self.assertNotIn("track", cs.captions_generate(chars_per_line=20))

    def test_unknown_arguments_and_error_results_are_flagged_to_the_client(self):
        refused = asyncio.run(cs.mcp.call_tool("captions_check", {"track": 1, "maxCharacters": 40}))
        self.assertTrue(refused.isError)
        with patch.object(cs, "timeline_ai", return_value=_err("Resolve is not running")):
            failed = asyncio.run(cs.mcp.call_tool("captions_get", {"track": 1}))
        self.assertTrue(failed.isError)

    def test_tool_bodies_run_off_the_event_loop(self):
        for tool in asyncio.run(cs.mcp.list_tools()):
            self.assertTrue(asyncio.iscoroutinefunction(cs.mcp._tool_manager.get_tool(tool.name).fn), tool.name)

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
        self.assertEqual(check.call_args.args[0], {"track": 1, "maxCharacters": 20, "minFramesPerWord": 3})

    def test_tool_surface_is_small_and_strict(self):
        tools = asyncio.run(cs.mcp.list_tools())
        self.assertEqual(sorted(t.name for t in tools),
                         ["captions_check", "captions_fix_text", "captions_generate", "captions_get", "captions_split",
                          "captions_style"])
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
            result = live.check_captions_live(Mock(EXPORT_DRT=1), timeline,
                                              {"track": 1, "maxCharacters": 20, "minFramesPerWord": 3}, lambda path: path)
        request = json.loads(run.call_args.kwargs["input"])
        self.assertEqual((request["operation"], request["track"], request["maxCharacters"], request["minFramesPerWord"]),
                         ("check_captions", 1, 20, 3))
        self.assertEqual(result["issues"], report["issues"])
        self.assertEqual(result["timeline"], "Cut")


if __name__ == "__main__":
    unittest.main()
