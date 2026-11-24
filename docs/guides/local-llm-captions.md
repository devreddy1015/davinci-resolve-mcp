# Captions with a local model (Ollama)

`src/captions_server.py` is a second entry point for small local models. It
exposes six tools with typed, closed inputs: about 3k characters of tool text
(names, descriptions and schemas, under a thousand tokens) instead of the
compound server's ~145k. Underneath it calls the same live
operations as `timeline_ai` (`create_subtitles`, `list_captions_live`,
`write_captions_live`, `set_subtitle_preset`), so Resolve sees the same
verified export → edit → import revisions. The model supplies text and names;
the server does the numbers.

| Tool | What the model sends | What the server does |
|---|---|---|
| `captions_generate(chars_per_line=18)` | nothing | Resolve Studio auto-captions on a new subtitle track; returns its index as `track` |
| `captions_get(track=1)` | nothing | returns `[{id, text}]`; timing stays on the server |
| `captions_fix_text(edits=[{id, text}], track=1)` | corrected text | re-times the words by letter count; new timeline revision |
| `captions_split(max_words=3, track=1)` | a card size | cuts long captions into cards at Resolve's word timings; a card under 3 frames per word stays joined to its neighbour |
| `captions_style(recipe, track=1)` | one of `social_bold`, `clean_minimal`, `youtube_standard` | applies Word Highlight with the recipe's inputs |
| `captions_check(track=1, max_characters=24)` | nothing | overlaps, gaps, word timing, long lines, cards under 3 frames per word |

The order is generate → get → fix_text → split → style → check, passing the `track`
that `captions_generate` returned to each later call. Each write creates a
new, selected timeline revision and keeps the original, as the compound server
does. Recipes live in `RECIPES` in `src/captions_server.py`; `social_bold` is
the look from issue #295 (white text, black outline on every word, a yellow box
behind the spoken word, three words per line). None of the three has been
checked visually in Resolve yet; tune the numbers there once, and every model
reproduces them.

## Setup (Windows)

Built for Windows first; on macOS and Linux use `venv/bin/python` where these
steps say `venv\Scripts\python`.

1. DaVinci Resolve Studio open with a project and timeline, and **Preferences >
   General > External scripting using** set to **Local**. Auto-captions need
   Studio.
2. Python 3.10+ and Node.js LTS on `PATH`, and Ollama running.
3. From the repository folder, run the installer. It creates `venv`, finds
   Resolve's scripting paths, and installs the Node packages the caption codec
   needs:

```powershell
python install.py --clients manual
```

4. Install the terminal chat client,
   [ollmcp](https://github.com/jonigl/mcp-client-for-ollama), into the same
   environment:

```powershell
venv\Scripts\python -m pip install ollmcp
```

5. Pull a model that can call tools (`ollama pull <model>`), then list the ones
   you have that qualify:

```powershell
venv\Scripts\python scripts\local_llm_captions.py
```

6. Start:

```powershell
venv\Scripts\python scripts\local_llm_captions.py -m <model>
```

The launcher checks Resolve's scripting library, Node and the codec packages,
and that Ollama has the model with tool calling. It writes
`logs\ollmcp-servers.json` with the same server entry `install.py` writes for
other clients (Resolve paths, and `PYTHONHOME` on Windows), then starts ollmcp
with it. `--check` stops before starting and prints the command instead.
`OLLAMA_HOST` is honoured, including `0.0.0.0:11434`. In ollmcp, keep the
context window at 8k or more; the six tools fit comfortably.

Then ask, for example: "Generate captions, fix any misspelt names, apply
social_bold to track 1, and check it."

## Compare a local model with Claude

The local model should give the same captions Claude does. To measure that:

1. Pick test clips that cover the hard cases: a talking head, two speakers,
   fast speech, names or jargon, speech over music.
2. Run each task with Claude on a copy of the timeline, check the result in
   Resolve, and export it as a `.drt` (File > Export > Timeline). That is the
   reference.
3. Run the same task with the local model on another copy and export that.
4. Score it:

```powershell
venv\Scripts\python scripts\compare_captions.py approved.drt local.drt --track 1
```

Resolve does not need to be open. The report gives the word error rate
(ignoring case and punctuation, plus an exact variant), the share of words
whose start lands within `--tolerance` frames (default 2), card counts, the
caption check, and how many style settings match the reference exactly, with
the mismatches named. It exits 1 when the local result misses a threshold:
more than 5% word errors, under 95% of words on time, any style setting
different, any caption error, or a card too short to read.

Tasks to start with, the same prompt for both models:

- "Fix spelling and punctuation in the captions on track 1 without changing the meaning, then check them."
- "Remove filler words (um, uh, you know) from the captions on track 1, then check them."
- "The speaker's name is <name>. Correct it wherever the captions get it wrong."
- "Split track 1 into 3-word cards, apply social_bold and check it."
- "Generate captions, fix them, split into 3-word cards, apply social_bold and check."

## Limits

- Per-word scale ("pop") and per-caption in/out transitions are not available;
  see [subtitle-track editing](subtitle-track-editing.md).
- `captions_fix_text` keeps each caption's time span; `captions_split` moves
  card boundaries only to Resolve's own word timings.
- Translation onto a second track is not built.
- This was tested against exported timeline files and a mocked Resolve, not a
  live Resolve or a live Ollama session.
