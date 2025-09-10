# Codex Log Viewer

A tiny Python tool that turns Codex JSONL session logs into a clean, self‑contained HTML page with a light pastel theme, Markdown rendering, collapsible sections, and a tiny inline code highlighter.

- Input: JSONL logs with events like `message`, `reasoning`, `function_call`, `function_call_output`.
- Output: One standalone HTML file with inline CSS/JS (UTF‑8, emoji safe).

## Features

- Markdown rendering for user, assistant, and reasoning text (`markdown-it-py`).
- Collapsible panels for reasoning summaries and function outputs with a toolbar to collapse/expand all.
- Fixed‑width blocks for function calls and outputs; long outputs scroll.
- Tiny client‑side highlighter for code fences in Markdown (json, python, bash/sh, diff).
- Hides encrypted reasoning content; shows only the summary text.

## Getting Started

1) Install dependencies

```bash
pip install -r requirements.txt
```

2) Render a log to HTML

```bash
# write to a file
python3 render_jsonl.py example.jsonl -o example.html

# or to stdout
python3 render_jsonl.py example.jsonl > example.html
```

3) Batch convert all logs from ~/.codex/sessions

```bash
# mirrors the ~/.codex/sessions tree into the current directory,
# converts every .jsonl to .html, and writes a top-level index.html
python3 render_jsonl.py --all

# optional: point to a different sessions dir
python3 render_jsonl.py --all --sessions-dir /path/to/sessions
```

4) Open the HTML in your browser

```bash
xdg-open example.html  # Linux
open example.html      # macOS
```

## Example Files

- Sample log: [example.jsonl](example.jsonl)
- Generated HTML: [example.html](https://htmlpreview.github.io/?https://github.com/dschwen/codex_log_viewer/blob/main/example.html)

## Notes

- Only reasoning and function output blocks are collapsible.
- Function outputs are shown as plain mono text (not Markdown) to avoid accidental formatting.
- The inline highlighter is intentionally minimal; it escapes HTML before injecting token spans.

## License

LGPL 2.0
