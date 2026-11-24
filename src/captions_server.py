"""Five strict caption tools for small local models (Ollama, LM Studio, llama.cpp).

The same live Resolve operations as the compound server's timeline_ai, but every
input is typed and closed: the look is a recipe name, and the server does the
numbers and the word re-timing, so a small model only ever supplies text and
names. About 2.6k characters of tool text against the compound server's ~145k.
Run it like the compound server:  python src/captions_server.py
"""
from __future__ import annotations

import os
import sys
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

_project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_dir not in sys.path:
    sys.path.insert(0, _project_dir)

from src.server import (  # noqa: E402
    _get_tl, _install_threaded_tool_dispatch, _resolve_safe_dir, get_resolve, timeline_ai,
)
from src.utils.mcp_error_flag import ErrorFlaggingFastMCP  # noqa: E402
from src.utils.mcp_stdio import run_fastmcp_stdio  # noqa: E402
from src.utils.subtitle_live import check_captions_live  # noqa: E402

# Word Highlight inputs applied on top of the bundled reference, so every recipe
# starts from the same base. social_bold is the #295 look; none has been checked
# visually in Resolve yet.
RECIPES = {
    "social_bold": {  # 9:16: white, black outline on every word, black spoken word on a yellow box
        "textRed": 1, "textGreen": 1, "textBlue": 1,
        "allWordsOutlineEnabled": 1, "allWordsOutlineRed": 0, "allWordsOutlineGreen": 0, "allWordsOutlineBlue": 0,
        "allWordsOutlineThickness": 0.03,
        "highlightRed": 0, "highlightGreen": 0, "highlightBlue": 0,
        "highlightBackgroundEnabled": 1, "highlightBackgroundRed": 1, "highlightBackgroundGreen": 0.85,
        "highlightBackgroundBlue": 0,
        "wordsPerLine": 3,
    },
    "clean_minimal": {  # white text, spoken word in the preset's yellow, no outlines or box
        "textRed": 1, "textGreen": 1, "textBlue": 1,
        "outlineEnabled": 0, "allWordsOutlineEnabled": 0, "highlightBackgroundEnabled": 0,
        "wordsPerLine": 4,
    },
    "youtube_standard": {  # 16:9 lower third: white with a thin black outline on every word
        "textRed": 1, "textGreen": 1, "textBlue": 1, "size": 0.05, "position": [0.5, 0.12],
        "allWordsOutlineEnabled": 1, "allWordsOutlineRed": 0, "allWordsOutlineGreen": 0, "allWordsOutlineBlue": 0,
        "allWordsOutlineThickness": 0.02, "highlightBackgroundEnabled": 0,
        "wordsPerLine": 5,
    },
}
Recipe = Literal["social_bold", "clean_minimal", "youtube_standard"]

# Same server class as the compound server: unknown arguments are refused and
# error results carry isError, instead of a weak model reading them as success.
mcp = ErrorFlaggingFastMCP("davinci-resolve-captions")


class TextEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Caption id from captions_get")
    text: str = Field(description="Corrected text for that caption")


def retime_words(text: str, start: float, end: float) -> list[dict]:
    """Share a caption's span between its words by letter count; the words tile [start, end] exactly."""
    words = text.split()
    if not words:
        raise ValueError("caption text is empty")
    total = sum(len(w) for w in words)
    out, at, done = [], start, 0
    for word in words:
        done += len(word)
        to = end if done == total else start + (end - start) * done / total
        out.append({"text": word, "start": at, "end": to})
        at = to
    return out


def _ai(action: str, params: dict) -> dict:
    # legacy = the plain dict; RESOLVE_MCP_RESULT_ENVELOPE=pure would nest it under "result".
    return timeline_ai(action, {**params, "envelope": "legacy"})


def _check(params: dict) -> dict:
    _project, timeline, err = _get_tl()
    return err or check_captions_live(get_resolve(), timeline, params, _resolve_safe_dir)


@mcp.tool()
def captions_generate(chars_per_line: Annotated[int, Field(ge=1, le=60)] = 18) -> dict:
    """Create captions from the timeline's audio (Resolve Studio) on a new subtitle track.
    Returns that track's index as `track`; pass it to the other tools."""
    result = _ai("create_subtitles", {"settings": {"chars_per_line": chars_per_line}})
    if result.get("verified"):
        # shortcut: assumes Resolve appends the new subtitle track last; upgrade if a build inserts elsewhere.
        return {**result, "track": result["subtitle_tracks_after"]}
    return result


@mcp.tool()
def captions_get(track: int = 1) -> dict:
    """List a subtitle track's captions as {id, text}, in order. Timing stays on the server."""
    result = _ai("list_captions_live", {"track": track})
    if not result.get("success"):
        return result
    return {"success": True, "captions": [{"id": c["id"], "text": c["text"]} for c in result["captions"]]}


@mcp.tool()
def captions_fix_text(edits: list[TextEdit], track: int = 1) -> dict:
    """Replace caption text (spelling, names, punctuation, fillers); keep each caption's meaning and length.
    The server re-times the words and saves a new timeline revision; the original timeline is kept."""
    current = _ai("list_captions_live", {"track": track})
    if not current.get("success"):
        return current
    by_id = {c["id"]: c for c in current["captions"]}
    unknown = [e.id for e in edits if e.id not in by_id]
    if unknown:
        return {"success": False, "error": f"Unknown caption ids {unknown}; use ids from captions_get"}
    try:
        replace = [{"id": e.id, "text": " ".join(e.text.split()), "start": by_id[e.id]["start"],
                    "end": by_id[e.id]["end"], "words": retime_words(e.text, by_id[e.id]["start"], by_id[e.id]["end"])}
                   for e in edits]
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    return _ai("write_captions_live", {"track": track, "replace": replace})


@mcp.tool()
def captions_style(recipe: Recipe, track: int = 1) -> dict:
    """Apply a named look to a subtitle track: social_bold (9:16, outline on every word, box behind the spoken
    word), clean_minimal, youtube_standard (16:9 lower third). Saves a new timeline revision; the original is kept."""
    return _ai("set_subtitle_preset", {"track": track, "preset": "Word Highlight", "inputs": RECIPES[recipe]})


@mcp.tool()
def captions_check(track: int = 1, max_characters: Annotated[int, Field(ge=1)] = 24) -> dict:
    """Check a subtitle track for overlaps, gaps, bad word timing and lines over max_characters. Read-only."""
    return _check({"track": track, "maxCharacters": max_characters})


# Tool bodies block on Resolve and on the Node codec; keep them off the event loop.
_install_threaded_tool_dispatch(mcp)

if __name__ == "__main__":
    run_fastmcp_stdio(mcp)
