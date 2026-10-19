# Running the pipeline on Claude (Fable) instead of Gemini

Any model name starting with `claude` is routed to the Anthropic Messages API
(`sceneiq/claude_backend.py`). Structured calls use a forced tool call; searches use
Anthropic's server-side web search tool, converted to the same format as Gemini grounding.

Add `ANTHROPIC_API_KEY=...` to `.env` (gitignored), then run:

```bash
SCENEIQ_DEEP_MODEL=claude-fable-5-1 SCENEIQ_RESEARCH_MODEL=claude-fable-5-1 \
  .venv/bin/python -m sceneiq "Step Brothers (2008)" --annotation --workers 8 -o data/outputs_v15
```

- `SCENEIQ_DEEP_MODEL` sets the writer, validation, polish, mining, corroboration checks and the search sweep.
- `SCENEIQ_RESEARCH_MODEL` sets the per-fact searches.
- Fast structuring steps stay on Gemini Flash (`SCENEIQ_FAST_MODEL`); set it to a claude model to move those too.
- `GEMINI_API_KEY` is still required: YouTube video notes always use Gemini (`SCENEIQ_VIDEO_MODEL`).
- Tuning: `SCENEIQ_CLAUDE_SEARCHES` (web searches per call, default 6), `SCENEIQ_CLAUDE_CONCURRENCY` (default 8).
