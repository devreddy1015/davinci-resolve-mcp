# Captions with a local model (Ollama)

`src/captions_server.py` is a second entry point for small local models. It
exposes five tools with typed, closed inputs: about 2.6k characters of tool
text (names, descriptions and schemas, under a thousand tokens) instead of the
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
| `captions_style(recipe, track=1)` | one of `social_bold`, `clean_minimal`, `youtube_standard` | applies Word Highlight with the recipe's inputs |
| `captions_check(track=1, max_characters=24)` | nothing | overlaps, gaps, word timing, long lines |

The order is generate → get → fix_text → style → check, passing the `track`
that `captions_generate` returned to each later call. Each write creates a
new, selected timeline revision and keeps the original, as the compound server
does. Recipes live in `RECIPES` in `src/captions_server.py`; `social_bold` is
the look from issue #295 (white text, black outline on every word, a yellow box
behind the spoken word, three words per line). None of the three has been
checked visually in Resolve yet; tune the numbers there once, and every model
reproduces them.

## Setup

1. Resolve Studio open with a project and timeline, external scripting set to
   Local, as for the compound server. Auto-captions need Studio.
2. Node and the codec packages: `cd resolve-advanced && npm install --omit=dev --omit=optional`.
3. Ollama with a model that Ollama tags `tools` (tool calling). Keep the
   context window at 8k or more; the five tools fit comfortably.
4. A terminal MCP client. [ollmcp](https://github.com/jonigl/mcp-client-for-ollama)
   is maintained and takes the usual `mcpServers` JSON:

```json
{
  "mcpServers": {
    "resolve-captions": {
      "command": "<path>/venv/bin/python",
      "args": ["<path>/src/captions_server.py"]
    }
  }
}
```

```bash
uvx ollmcp --servers-json servers.json --model <model-name>
```

Then ask, for example: "Generate captions, fix any misspelt names, apply
social_bold to track 1, and check it."

## Limits

- Per-word scale ("pop") and per-caption in/out transitions are not available;
  see [subtitle-track editing](subtitle-track-editing.md).
- `captions_fix_text` keeps each caption's time span. To change timing or split
  cards, use the compound server's `write_captions_live`.
- This was tested against exported timeline files and a mocked Resolve, not a
  live Resolve or a live Ollama session.
