#!/usr/bin/env python3
"""Score a local model's captions against Claude's approved ones, from two exported timelines.

Export the timeline Claude produced (and you approved) and the one the local model produced,
both as .drt (File > Export > Timeline), then:

    python scripts/compare_captions.py approved.drt local.drt [--track 1] [--candidate-track 1] [--tolerance 2]

Prints a JSON report and exits 1 when the local result misses a threshold. Needs Node and the
resolve-advanced packages, like the live caption tools; Resolve does not need to be open.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

THRESHOLDS = {"word_error_rate": 0.05, "timing_within_tolerance": 0.95}
MIN_FRAMES_PER_WORD = 3  # same readability floor as captions_check


def _words(track: dict, exact: bool) -> list[tuple[str, float]]:
    """(token, start frame) for every word; normalised tokens ignore case and punctuation."""
    out = []
    for caption in track["captions"]:
        for word in caption["words"]:
            token = word["text"] if exact else re.sub(r"[^\w']", "", word["text"].lower())
            if token:
                out.append((token, word.get("start")))
    return out


def _edit_distance(a: list, b: list) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def _error_rate(reference: dict, candidate: dict, exact: bool) -> float:
    ref = [t for t, _ in _words(reference, exact)]
    return _edit_distance(ref, [t for t, _ in _words(candidate, exact)]) / max(len(ref), 1)


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, abs_tol=1e-6)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _style(reference: dict | None, candidate: dict | None) -> tuple[float | None, dict]:
    if reference is None:
        return None, {}
    if candidate is None or candidate["templateId"] != reference["templateId"]:
        return 0.0, {"templateId": [reference["templateId"], candidate and candidate["templateId"]]}
    wanted, got = reference["inputs"], candidate["inputs"]
    mismatches = {k: [v, got.get(k)] for k, v in wanted.items() if not _same(v, got.get(k))}
    return (1 - len(mismatches) / len(wanted) if wanted else 1.0), mismatches


def score(reference: dict, candidate: dict, *, tolerance: float = 2) -> dict:
    """Compare two decoded tracks: {"captions": [...], "issues": [...], "preset": {templateId, inputs} | None}."""
    ref, cand = _words(reference, False), _words(candidate, False)
    matcher = SequenceMatcher(None, [t for t, _ in ref], [t for t, _ in cand], autojunk=False)
    pairs = [(ref[i + k][1], cand[j + k][1]) for i, j, n in matcher.get_matching_blocks() for k in range(n)]
    errors = [abs(a - b) for a, b in pairs if a is not None and b is not None]  # untimed words still count for WER
    timing = sum(e <= tolerance for e in errors) / len(errors) if errors else 0.0
    word_error_rate = _error_rate(reference, candidate, False)
    style, mismatches = _style(reference.get("preset"), candidate.get("preset"))
    issues = candidate.get("issues", [])
    check = {"errors": [i["code"] for i in issues if i.get("severity") != "warning"],
             "warnings": dict(Counter(i["code"] for i in issues if i.get("severity") == "warning"))}

    failed = []
    if word_error_rate > THRESHOLDS["word_error_rate"]:
        failed.append("word_error_rate")
    if timing < THRESHOLDS["timing_within_tolerance"]:
        failed.append("timing_within_tolerance")
    if style is not None and style < 1:
        failed.append("style_match")
    if check["errors"]:
        failed.append("check_errors")
    if "too_short" in check["warnings"]:
        failed.append("too_short")
    return {
        "word_error_rate": word_error_rate,
        "word_error_rate_exact": _error_rate(reference, candidate, True),
        "timing_within_tolerance": timing,
        "timing_max_error_frames": max(errors, default=None),
        "cards": {"reference": len(reference["captions"]), "candidate": len(candidate["captions"]),
                  "max_words_per_card": max((len(c["words"]) for c in candidate["captions"]), default=0)},
        "check": check,
        "style_match": style,
        "style_mismatches": mismatches,
        "passed": not failed,
        "failed": failed,
    }


def load(path: str, track: int) -> dict:
    """Decode one subtitle track of an exported .drt through the same codec the live tools use."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.utils import subtitle_live as live

    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required: it runs the caption codec")
    gap = live.subtitle_codec_gap()
    if gap:
        raise RuntimeError(gap)
    request = {"source": str(Path(path).resolve()), "track": track}
    captions = live._codec(node, {"operation": "inspect_captions", **request})["captions"]
    issues = live._codec(node, {"operation": "check_captions", "minFramesPerWord": MIN_FRAMES_PER_WORD, **request})
    try:
        preset = live._codec(node, {"operation": "inspect", **request})
    except RuntimeError as exc:
        if "No animation preset" not in str(exc):
            raise
        preset = None
    return {"captions": captions, "issues": issues["issues"], "preset": preset}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a local model's captions against approved ones.")
    parser.add_argument("reference", help="exported .drt with the approved (Claude) captions")
    parser.add_argument("candidate", help="exported .drt with the local model's captions")
    parser.add_argument("--track", type=int, default=1, help="subtitle track in the reference")
    parser.add_argument("--candidate-track", type=int, help="subtitle track in the candidate (default: --track)")
    parser.add_argument("--tolerance", type=float, default=2, help="word timing tolerance in frames")
    args = parser.parse_args(argv)
    try:
        report = score(load(args.reference, args.track), load(args.candidate, args.candidate_track or args.track),
                       tolerance=args.tolerance)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
