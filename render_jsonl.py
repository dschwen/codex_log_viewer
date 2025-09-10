#!/usr/bin/env python3

import json
import sys
import html
from pathlib import Path


def esc(text: str) -> str:
    """HTML-escape text, preserving Unicode (e.g., emoji)."""
    return html.escape(text, quote=False)


def render_reasoning(entry: dict) -> str:
    # Show only the redacted summary; hide encrypted content
    summary_parts = entry.get("summary") or []
    texts = []
    for part in summary_parts:
        if part.get("type") == "summary_text":
            # Replace newlines with <br/>
            texts.append(esc(part.get("text", "")).replace("\n", "<br/>"))
    if not texts:
        return ""
    content_html = "<br/>".join(texts)
    return f"""
    <div class='block reasoning'>
      <div class='label'>Reasoning (summary only)</div>
      <div class='text'>{content_html}</div>
    </div>
    """


def render_message(entry: dict) -> str:
    role = entry.get("role", "assistant")
    # Aggregate any text-like content items
    parts = entry.get("content") or []
    texts = []
    for part in parts:
        t = part.get("text")
        if not isinstance(t, str):
            continue
        texts.append(t)
    text = "\n\n".join(texts)
    text_html = esc(text).replace("\n", "<br/>")
    css_class = "user" if role == "user" else "assistant"
    label = "User" if role == "user" else "Assistant"
    return f"""
    <div class='block {css_class}'>
      <div class='label'>{label}</div>
      <div class='text'>{text_html}</div>
    </div>
    """


def parse_json_string_maybe(s):
    """Try to parse a JSON string that might already be a dict. Return (obj, ok)."""
    if isinstance(s, (dict, list)):
        return s, True
    if not isinstance(s, str):
        return None, False
    try:
        return json.loads(s), True
    except Exception:
        return s, False


def render_function_call(entry: dict) -> str:
    name = entry.get("name", "function")
    args_raw = entry.get("arguments")
    args_obj, ok = parse_json_string_maybe(args_raw)
    if ok and isinstance(args_obj, dict):
        # Try to present command nicely if present (e.g., shell tool)
        cmd = args_obj.get("command")
        if isinstance(cmd, list):
            # Render like a shell command line
            display_cmd = " ".join(str(c) for c in cmd)
            body = f"$ {esc(display_cmd)}"
        else:
            body = esc(json.dumps(args_obj, indent=2))
    else:
        body = esc(str(args_raw))

    return f"""
    <div class='block func-call'>
      <div class='label'>Function Call: {esc(name)}</div>
      <pre class='code'>{body}</pre>
    </div>
    """


def render_function_output(entry: dict) -> str:
    out_raw = entry.get("output")
    out_obj, ok = parse_json_string_maybe(out_raw)
    if ok and isinstance(out_obj, dict) and "output" in out_obj:
        body = out_obj.get("output")
    else:
        body = out_raw
    if not isinstance(body, str):
        try:
            body = json.dumps(body, indent=2)
        except Exception:
            body = str(body)
    return f"""
    <div class='block func-output'>
      <div class='label'>Function Output</div>
      <pre class='code'>{esc(body)}</pre>
    </div>
    """


def render_session_header(meta: dict, source_path: Path) -> str:
    # First line often contains session metadata
    parts = []
    if meta.get("id"):
        parts.append(f"<div><b>Session:</b> {esc(meta['id'])}</div>")
    if meta.get("timestamp"):
        parts.append(f"<div><b>Started:</b> {esc(meta['timestamp'])}</div>")
    git = meta.get("git") or {}
    if git:
        if git.get("repository_url"):
            parts.append(f"<div><b>Repo:</b> {esc(git['repository_url'])}</div>")
        if git.get("branch"):
            parts.append(f"<div><b>Branch:</b> {esc(git['branch'])}</div>")
        if git.get("commit_hash"):
            parts.append(f"<div><b>Commit:</b> <code>{esc(git['commit_hash'])}</code></div>")
    if not parts:
        return ""
    return f"""
    <div class='session'>
      <div class='title'>Codex Session Log</div>
      <div class='subtitle'>{esc(str(source_path))}</div>
      {''.join(parts)}
    </div>
    """


STYLE = """
/* Layout */
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #ffffff; color: #1f2937; }
body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Noto Sans, Ubuntu, Cantarell, 'Helvetica Neue', Arial, 'Apple Color Emoji', 'Segoe UI Emoji'; line-height: 1.5; }
.container { max-width: 920px; margin: 0 auto; padding: 24px 16px 80px; }
.session { background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; margin-bottom: 20px; }
.session .title { font-size: 24px; font-weight: 700; margin-bottom: 4px; }
.session .subtitle { color: #6b7280; margin-bottom: 8px; font-size: 13px; }

/* Blocks */
.block { border-radius: 12px; padding: 12px 14px; margin: 12px 0; border: 1px solid transparent; }
.block .label { font-size: 12px; font-weight: 600; letter-spacing: 0.02em; text-transform: uppercase; opacity: 0.8; margin-bottom: 6px; }
.block .text { white-space: normal; word-wrap: break-word; overflow-wrap: anywhere; }
.code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 12.5px; line-height: 1.45; background: rgba(0,0,0,0.02); border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px; overflow: auto; max-height: 380px; }

/* Pastel role/type colors */
.user { background: #e8f0fe; border-color: #d2e3fc; }
.user .label { color: #1a73e8; }

.assistant { background: #e6f4ea; border-color: #ccead6; }
.assistant .label { color: #1e8e3e; }

.reasoning { background: #f3e8ff; border-color: #e9d5ff; }
.reasoning .label { color: #7c3aed; }

.func-call { background: #fff7e6; border-color: #ffe8bf; }
.func-call .label { color: #b45309; }

.func-output { background: #f5f5f5; border-color: #e5e7eb; }
.func-output .label { color: #374151; }

/* Minor tweaks */
a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }
"""


def render_jsonl_to_html(filepath: str) -> str:
    p = Path(filepath)
    blocks = []
    session_header_done = False
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # If any malformed line, show it raw in output section to avoid hard-fail
                    blocks.append(
                        f"<div class='block func-output'><div class='label'>Unparsed Line</div><pre class='code'>{esc(line)}</pre></div>"
                    )
                    continue

                # Try to render session header from the very first meta-like entry
                if not session_header_done and ("timestamp" in entry or "git" in entry or "instructions" in entry):
                    blocks.append(render_session_header(entry, p))
                    session_header_done = True
                    continue

                # Skip state bookkeeping
                if entry.get("record_type") == "state":
                    continue

                typ = entry.get("type")
                if typ == "reasoning":
                    blocks.append(render_reasoning(entry))
                elif typ == "message":
                    blocks.append(render_message(entry))
                elif typ == "function_call":
                    blocks.append(render_function_call(entry))
                elif typ == "function_call_output":
                    blocks.append(render_function_output(entry))
                else:
                    # Fallback generic renderer
                    blocks.append(
                        f"<div class='block func-output'><div class='label'>Event: {esc(str(typ))}</div><pre class='code'>{esc(json.dumps(entry, indent=2))}</pre></div>"
                    )
    except FileNotFoundError:
        raise

    html_doc = f"""
<!DOCTYPE html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Codex Session Log — {esc(p.name)}</title>
  <style>{STYLE}</style>
</head>
<body>
  <div class='container'>
    {''.join(b for b in blocks if b)}
  </div>
</body>
</html>
"""
    return html_doc


def main(argv):
    if len(argv) < 2:
        print("Usage: render_jsonl.py <your_codex_log.jsonl> [-o output.html]", file=sys.stderr)
        sys.exit(1)
    input_path = argv[1]
    out_path = None
    if len(argv) >= 4 and argv[2] in ("-o", "--output"):
        out_path = argv[3]

    try:
        html_doc = render_jsonl_to_html(input_path)
    except FileNotFoundError:
        print(f"Error: The file {input_path} was not found.", file=sys.stderr)
        sys.exit(2)

    if out_path:
        Path(out_path).write_text(html_doc, encoding="utf-8")
    else:
        # Default to stdout
        sys.stdout.reconfigure(encoding='utf-8')
        print(html_doc)


if __name__ == "__main__":
    main(sys.argv)
