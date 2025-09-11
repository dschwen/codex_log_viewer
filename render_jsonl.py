#!/usr/bin/env python3

import json
import sys
import html
import os
import re
import base64
import datetime as _dt
from collections import defaultdict
from pathlib import Path

# Optional markdown rendering for reasoning blocks
try:
    from markdown_it import MarkdownIt  # type: ignore
except Exception:  # pragma: no cover
    MarkdownIt = None


def esc(text: str) -> str:
    """HTML-escape text, preserving Unicode (e.g., emoji)."""
    return html.escape(text, quote=False)

_SCRIPT_TAG_RE = re.compile(r"(?is)<\s*/?\s*script\b")

def sanitize_html(raw: str) -> str:
    """Neutralize script tags that might slip through Markdown rendering.
    Replaces the opening angle bracket of <script ...> and </script> with &lt;.
    """
    if not raw:
        return raw
    return _SCRIPT_TAG_RE.sub(lambda m: "&lt;" + m.group(0)[1:], raw)


def _pretty_timestamp(ts_raw) -> str:
    """Format an ISO-like timestamp into 'YYYY-MM-DD HH:MM:SS'.
    Falls back to the original string if parsing fails.
    """
    if not ts_raw:
        return ""
    s = str(ts_raw)
    try:
        # Handle trailing 'Z' as UTC
        s2 = s.replace("Z", "+00:00") if isinstance(s, str) else s
        dt = _dt.datetime.fromisoformat(s2)
        # Keep provided timezone if any; just format date & time
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        # Fallback: best-effort extract
        m = re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2}):(\d{2})", s)
        if m:
            return f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}"
        return s


def render_reasoning(entry: dict, ts_inline: str = "") -> str:
    # Show only the redacted summary; hide encrypted content
    summary_parts = entry.get("summary") or []
    raw_texts = []
    for part in summary_parts:
        if part.get("type") == "summary_text":
            raw_texts.append(part.get("text", ""))
    if not raw_texts:
        return ""
    joined = "\n\n".join(raw_texts)
    if MarkdownIt is not None:
        md = MarkdownIt("commonmark")
        md.options["html"] = False  # explicit: do not allow raw HTML
        content_html = sanitize_html(md.render(joined))
    else:
        content_html = esc(joined).replace("\n", "<br/>")
    return f"""
    <div class='block reasoning collapsible'>
      <div class='label-row'>
        <div class='label'>Reasoning (summary only)</div>
        <div class='actions'>
          {ts_inline}
          <button class='toggle' type='button' aria-expanded='true'>Collapse</button>
        </div>
      </div>
      <div class='collapsible-content'>
        <div class='text markdown'>{content_html}</div>
      </div>
    </div>
    """


def render_message(entry: dict, ts_inline: str = "") -> str:
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
    if MarkdownIt is not None:
        md = MarkdownIt("commonmark")
        md.options["html"] = False
        text_html = sanitize_html(md.render(text))
    else:
        text_html = esc(text).replace("\n", "<br/>")
    css_class = "user" if role == "user" else "assistant"
    label = "User" if role == "user" else "Assistant"
    return f"""
    <div class='block {css_class}'>
      <div class='label-row'>
        <div class='label'>{label}</div>
        <div class='actions'>{ts_inline}</div>
      </div>
      <div class='text markdown'>{text_html}</div>
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


def render_plan_update(args_obj: dict, ts_inline: str = "") -> str:
    explanation = (args_obj or {}).get("explanation") or ""
    plan_items = (args_obj or {}).get("plan") or []

    def sym(status: str) -> str:
        s = (status or "").lower()
        if s == "completed":
            return "✅"
        if s == "in_progress":
            return "⏳"
        return "☐"  # pending or unknown

    items_html = []
    for it in plan_items:
        step = (it or {}).get("step", "")
        status = (it or {}).get("status", "pending")
        items_html.append(f"<li><span class='plan-sym'>{sym(status)}</span> {esc(step)}</li>")

    if MarkdownIt is not None and explanation:
        md = MarkdownIt("commonmark")
        md.options["html"] = False
        expl_html = sanitize_html(md.render(explanation))
    else:
        expl_html = esc(explanation).replace("\n", "<br/>") if explanation else ""

    return f"""
    <div class='block plan'>
      <div class='label-row'>
        <div class='label'>Plan Update</div>
        <div class='actions'>{ts_inline}</div>
      </div>
      {('<div class="text markdown">' + expl_html + '</div>') if expl_html else ''}
      <ul class='plan-list'>
        {''.join(items_html)}
      </ul>
    </div>
    """


def render_function_call(entry: dict, ts_inline: str = "") -> str:
    name = entry.get("name", "function")
    args_raw = entry.get("arguments")
    args_obj, ok = parse_json_string_maybe(args_raw)

    # Special: update_plan → checklist
    if name == "update_plan" and ok and isinstance(args_obj, dict):
        return render_plan_update(args_obj, ts_inline)

    # Special: apply_patch diff highlighting
    def extract_patch_from_command(cmd_list):
        if not isinstance(cmd_list, list):
            return None
        if len(cmd_list) >= 2 and str(cmd_list[0]) == "apply_patch":
            return str(cmd_list[1])
        import re as _re
        for part in cmd_list:
            s = str(part)
            m = _re.search(r"\*\*\* Begin Patch(.*)\*\*\* End Patch", s, flags=_re.DOTALL)
            if m:
                return "*** Begin Patch" + m.group(1) + "*** End Patch"
        return None

    if ok and isinstance(args_obj, dict):
        cmd = args_obj.get("command")
        patch_text = extract_patch_from_command(cmd)
        if patch_text is not None:
            raw_b64 = base64.b64encode(patch_text.encode("utf-8")).decode("ascii")
            return f"""
    <div class='block func-call collapsible apply-patch' data-raw-b64='{esc(raw_b64)}'>
      <div class='label-row'>
        <div class='label'>Apply Patch</div>
        <div class='actions'>
          {ts_inline}
          <button class='toggle' type='button' aria-expanded='true'>Collapse</button>
          <button class='copy' type='button' title='Copy patch to clipboard'>Copy</button>
        </div>
      </div>
      <div class='collapsible-content'>
        <pre class='code diff'><code class='language-diff'>{esc(patch_text)}</code></pre>
      </div>
    </div>
    """

    # Default rendering of function call
    if ok and isinstance(args_obj, dict):
        cmd = args_obj.get("command")
        if isinstance(cmd, list):
            display_cmd = " ".join(str(c) for c in cmd)
            body = f"$ {esc(display_cmd)}"
        else:
            body = esc(json.dumps(args_obj, indent=2))
    else:
        body = esc(str(args_raw))

    return f"""
    <div class='block func-call'>
      <div class='label-row'>
        <div class='label'>Function Call: {esc(name)}</div>
        <div class='actions'>{ts_inline}</div>
      </div>
      <pre class='code'>{body}</pre>
    </div>
    """


def render_function_output(entry: dict, ts_inline: str = "") -> str:
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
    # Skip trivial confirmation after plan updates
    if isinstance(body, str) and body.strip() == "Plan updated":
        return ""
    # Determine if output is large; collapse by default when very large
    try:
        char_thresh = int(os.environ.get("COLLAPSE_OUTPUT_CHAR_THRESHOLD", "15000"))
    except Exception:
        char_thresh = 15000
    try:
        line_thresh = int(os.environ.get("COLLAPSE_OUTPUT_LINE_THRESHOLD", "300"))
    except Exception:
        line_thresh = 300
    is_large = (len(body) >= char_thresh) or (body.count("\n") + 1 >= line_thresh)
    collapsed_class = " collapsed" if is_large else ""
    aria_expanded = "false" if is_large else "true"
    toggle_label = "Expand" if is_large else "Collapse"
    return f"""
    <div class='block func-output collapsible{collapsed_class}'>
      <div class='label-row'>
        <div class='label'>Function Output</div>
        <div class='actions'>
          {ts_inline}
          <button class='toggle' type='button' aria-expanded='{aria_expanded}'>{toggle_label}</button>
        </div>
      </div>
      <div class='collapsible-content'>
        <pre class='code'>{esc(body)}</pre>
      </div>
    </div>
    """


def render_session_header(meta: dict, source_path: Path, ts_inline: str = "") -> str:
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
      <div class='header-row'>
        <div class='title'>Codex Session Log</div>
        {ts_inline}
      </div>
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
.session .header-row { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 4px; }

/* Toolbar */
.toolbar { display: flex; gap: 10px; align-items: center; margin: 12px 0 18px; font-size: 13px; }
.toolbar a { color: #374151; background: #f3f4f6; border: 1px solid #e5e7eb; padding: 6px 10px; border-radius: 8px; text-decoration: none; }
.toolbar a:hover { background: #eef2ff; border-color: #dbeafe; color: #1d4ed8; }
.filters { display: inline-flex; gap: 8px; flex-wrap: wrap; margin-left: 12px; }
.filter-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: 8px; border: 1px solid #e5e7eb; background: #f9fafb; color: #111827; cursor: pointer; user-select: none; }
.filter-chip input { accent-color: #2563eb; }
.chip-user { background: #e8f0fe; border-color: #d2e3fc; }
.chip-assistant { background: #e6f4ea; border-color: #ccead6; }
.chip-reasoning { background: #f3e8ff; border-color: #e9d5ff; }
.chip-func-call { background: #fff7e6; border-color: #ffe8bf; }
.chip-func-output { background: #f5f5f5; border-color: #e5e7eb; }
.chip-plan { background: #e0f2fe; border-color: #bae6fd; }

/* Blocks */
.block { border-radius: 12px; padding: 12px 14px; margin: 12px 0; border: 1px solid transparent; }
.block .label { font-size: 12px; font-weight: 600; letter-spacing: 0.02em; text-transform: uppercase; opacity: 0.8; margin-bottom: 6px; }
.block .text { white-space: normal; word-wrap: break-word; overflow-wrap: anywhere; }
.code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 12.5px; line-height: 1.45; background: rgba(0,0,0,0.02); border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px; overflow: auto; max-height: 380px; }
/* Rows */
.label-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.label-row .actions { display: flex; align-items: center; gap: 6px; }
.ts-inline { font-size: 12px; color: #6b7280; opacity: 0.9; }

/* Markdown content styling */
.markdown { font-size: 14px; }
.markdown p { margin: 0.3em 0 0.8em; }
.markdown h1, .markdown h2, .markdown h3 { margin: 0.6em 0 0.4em; line-height: 1.25; }
.markdown ul, .markdown ol { padding-left: 1.3em; margin: 0.3em 0 0.8em; }
.markdown code { background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 4px; padding: 0.1em 0.35em; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 12.5px; }
.markdown pre { background: rgba(0,0,0,0.02); border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px; overflow: auto; }
.markdown pre code { background: transparent; border: 0; padding: 0; display: block; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 12.5px; }
.markdown blockquote { border-left: 3px solid #e5e7eb; padding-left: 12px; color: #4b5563; margin: 0.3em 0 0.8em; }

/* Collapsible */
.toggle { font-size: 12px; border: 1px solid #e5e7eb; background: #ffffff; border-radius: 6px; padding: 4px 8px; color: #374151; cursor: pointer; }
.toggle:hover { background: #f3f4f6; }
.collapsible .collapsible-content { display: block; }
.collapsible.collapsed .collapsible-content { display: none; }

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

.plan { background: #e0f2fe; border-color: #bae6fd; }
.plan .label { color: #0284c7; }
.plan-list { list-style: none; padding-left: 0; margin: 6px 0 0; }
.plan-list li { margin: 4px 0; }
.plan-sym { display: inline-block; width: 1.4em; }

/* Apply patch tweaks */
.apply-patch .actions { display: flex; gap: 6px; }
.copy { font-size: 12px; border: 1px solid #e5e7eb; background: #ffffff; border-radius: 6px; padding: 4px 8px; color: #374151; cursor: pointer; }
.copy:hover { background: #f3f4f6; }
.code.diff, .markdown pre code.language-diff, pre.code > code.language-diff { line-height: 1.2; }

/* Minor tweaks */
a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }
 
/* Tiny highlighter token styles */
.tok-str { color: #b45309; }
.tok-com { color: #6b7280; font-style: italic; }
.tok-kw  { color: #2563eb; }
.tok-num { color: #7c3aed; }
.tok-line { display: block; line-height: 1.2; padding: 2px 6px; margin: 0; }
.tok-add  { color: #065f46; background: #ecfdf5; border-radius: 4px; }
.tok-del  { color: #991b1b; background: #fef2f2; border-radius: 4px; }
.tok-meta { color: #1f2937; background: #e5e7eb; border-radius: 4px; }

/* Filters toggle body classes to hide certain blocks */
.hide-user .block.user { display: none; }
.hide-assistant .block.assistant { display: none; }
.hide-reasoning .block.reasoning { display: none; }
.hide-func-call .block.func-call { display: none; }
.hide-func-output .block.func-output { display: none; }
.hide-plan .block.plan { display: none; }
"""


def render_jsonl_to_html(filepath: str) -> str:
    p = Path(filepath)
    blocks = []
    session_header_done = False
    wrapped_mode = None  # Unknown until first non-empty, parsed line
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw_obj = json.loads(line)
                except json.JSONDecodeError:
                    # If any malformed line, show it raw in output section to avoid hard-fail
                    blocks.append(
                        f"<div class='block func-output'><div class='label'>Unparsed Line</div><pre class='code'>{esc(line)}</pre></div>"
                    )
                    continue

                # Detect wrapped format: { timestamp, payload }
                if wrapped_mode is None:
                    if isinstance(raw_obj, dict) and "payload" in raw_obj and "timestamp" in raw_obj:
                        wrapped_mode = True
                    else:
                        wrapped_mode = False

                if wrapped_mode:
                    ts_raw = (raw_obj or {}).get("timestamp")
                    entry = (raw_obj or {}).get("payload") or {}
                    ts_pretty = _pretty_timestamp(ts_raw)
                    ts_inline = f"<span class='ts-inline'>{esc(ts_pretty)}</span>" if ts_pretty else ""
                else:
                    entry = raw_obj
                    ts_inline = ""

                # Try to render session header from the very first meta-like entry
                if not session_header_done and ("timestamp" in entry or "git" in entry or "instructions" in entry):
                    blocks.append(render_session_header(entry, p, ts_inline))
                    session_header_done = True
                    continue

                # Skip state bookkeeping
                if entry.get("record_type") == "state":
                    continue

                typ = entry.get("type")
                if typ == "reasoning":
                    blocks.append(render_reasoning(entry, ts_inline))
                elif typ == "message":
                    blocks.append(render_message(entry, ts_inline))
                elif typ == "function_call":
                    blocks.append(render_function_call(entry, ts_inline))
                elif typ == "function_call_output":
                    blocks.append(render_function_output(entry, ts_inline))
                else:
                    # Fallback generic renderer
                    blocks.append(
                        f"<div class='block func-output'><div class='label'>Event: {esc(str(typ))}</div><pre class='code'>{esc(json.dumps(entry, indent=2))}</pre></div>"
                    )
    except FileNotFoundError:
        raise

    # Top toolbar with collapse/expand all
    toolbar = """
    <div class='toolbar'>
      <a href="#" id="collapse-all">Collapse All</a>
      <a href="#" id="expand-all">Expand All</a>
      <div class='filters' title='Show/Hide blocks'>
        <label class='filter-chip chip-user'><input type='checkbox' data-class='user' checked /> User</label>
        <label class='filter-chip chip-assistant'><input type='checkbox' data-class='assistant' checked /> Assistant</label>
        <label class='filter-chip chip-reasoning'><input type='checkbox' data-class='reasoning' checked /> Reasoning</label>
        <label class='filter-chip chip-func-call'><input type='checkbox' data-class='func-call' checked /> Calls</label>
        <label class='filter-chip chip-func-output'><input type='checkbox' data-class='func-output' checked /> Outputs</label>
        <label class='filter-chip chip-plan'><input type='checkbox' data-class='plan' checked /> Plans</label>
      </div>
    </div>
    """

    script_js = r"""
  <script>
    (function() {
      function setCollapsed(el, collapsed) {
        if (!el) return;
        if (collapsed) el.classList.add('collapsed'); else el.classList.remove('collapsed');
        var btn = el.querySelector('.toggle');
        if (btn) {
          btn.setAttribute('aria-expanded', String(!collapsed));
          btn.textContent = collapsed ? 'Expand' : 'Collapse';
        }
      }

      document.addEventListener('click', function(ev) {
        var t = ev.target;
        if (t && t.closest) {
          var toggle = t.closest('.toggle');
          if (toggle) {
            ev.preventDefault();
            var box = toggle.closest('.collapsible');
            if (box) setCollapsed(box, !box.classList.contains('collapsed'));
            return;
          }
          var collapseAll = t.closest('#collapse-all');
          if (collapseAll) {
            ev.preventDefault();
            document.querySelectorAll('.collapsible').forEach(function(el){ setCollapsed(el, true); });
            return;
          }
          var expandAll = t.closest('#expand-all');
          if (expandAll) {
            ev.preventDefault();
            document.querySelectorAll('.collapsible').forEach(function(el){ setCollapsed(el, false); });
            return;
          }

          var copyBtn = t.closest('.copy');
          if (copyBtn) {
            ev.preventDefault();
            var box = copyBtn.closest('.apply-patch');
            if (!box) return;
            var b64 = box.getAttribute('data-raw-b64') || '';
            try {
              var raw = atob(b64);
              if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(raw).then(function(){
                  var old = copyBtn.textContent; copyBtn.textContent = 'Copied!';
                  setTimeout(function(){ copyBtn.textContent = old; }, 1200);
                }).catch(function(){
                  // fallback
                  var ta = document.createElement('textarea');
                  ta.value = raw; document.body.appendChild(ta); ta.select();
                  try { document.execCommand('copy'); } catch(e) {}
                  document.body.removeChild(ta);
                  var old = copyBtn.textContent; copyBtn.textContent = 'Copied!';
                  setTimeout(function(){ copyBtn.textContent = old; }, 1200);
                });
              } else {
                var ta = document.createElement('textarea');
                ta.value = raw; document.body.appendChild(ta); ta.select();
                try { document.execCommand('copy'); } catch(e) {}
                document.body.removeChild(ta);
                var old = copyBtn.textContent; copyBtn.textContent = 'Copied!';
                setTimeout(function(){ copyBtn.textContent = old; }, 1200);
              }
            } catch (e) {}
            return;
          }
        }
      }, false);

      // Filters
      function applyFilterState() {
        document.querySelectorAll('.filters input[type="checkbox"][data-class]').forEach(function(cb){
          var cls = cb.getAttribute('data-class');
          if (!cls) return;
          document.body.classList.toggle('hide-' + cls, !cb.checked);
        });
      }
      document.addEventListener('change', function(ev){
        var cb = ev.target && ev.target.closest && ev.target.closest('.filters input[type="checkbox"][data-class]');
        if (cb) applyFilterState();
      });
      document.addEventListener('DOMContentLoaded', applyFilterState);

      // Tiny inline syntax highlighter
      function escapeHtml(s) {
        return s.replace(/[&<>]/g, function(c){ return c === '&' ? '&amp;' : (c === '<' ? '&lt;' : '&gt;'); });
      }
      function highlightText(text, lang) {
        var esc = escapeHtml(text);
        if (lang === 'diff') {
          var lines = esc.split('\n');
          return lines.map(function(l){
            var cls = 'tok-line';
            if (l.startsWith('+')) cls += ' tok-add';
            else if (l.startsWith('-')) cls += ' tok-del';
            else if (l.startsWith('@')) cls += ' tok-meta';
            else cls += ' tok-none';
            return "<span class='" + cls + "'>" + l + "</span>";
          }).join('');
        }
        var s = esc;
        // Strings
        s = s.replace(/'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"/g, function(m){ return "<span class='tok-str'>"+m+"</span>"; });
        // Comments
        if (lang === 'python' || lang === 'py' || lang === 'bash' || lang === 'sh' || lang === 'shell') {
          s = s.replace(/(^|\s)(#.*)$/gm, function(_, p1, p2){ return p1 + "<span class='tok-com'>"+p2+"</span>"; });
        }
        // JSON booleans/null and numbers
        if (lang === 'json') {
          s = s.replace(/\b(true|false|null)\b/g, "<span class='tok-kw'>$1</span>");
          s = s.replace(/\b-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g, "<span class='tok-num'>$&</span>");
        }
        // Python keywords
        if (lang === 'python' || lang === 'py') {
          var kw = /\b(False|True|None|def|class|return|if|elif|else|for|while|try|except|finally|with|as|import|from|pass|break|continue|yield|lambda|global|nonlocal|assert|raise|in|is|and|or|not)\b/g;
          s = s.replace(kw, "<span class='tok-kw'>$1</span>");
        }
        // Shell options
        if (lang === 'bash' || lang === 'sh' || lang === 'shell') {
          s = s.replace(/(^|\s)(-[a-zA-Z][a-zA-Z0-9-]*)/g, "$1<span class='tok-kw'>$2</span>");
        }
        return s;
      }
      function highlightAll() {
        document.querySelectorAll('.markdown pre code, pre.code > code[class*="language-"]').forEach(function(code){
          var cls = code.className || '';
          var m = cls.match(/language-([a-z0-9]+)/i);
          var lang = m ? m[1].toLowerCase() : '';
          var txt = code.textContent || '';
          code.innerHTML = highlightText(txt, lang);
        });
      }
      document.addEventListener('DOMContentLoaded', highlightAll);
    })();
  </script>
"""

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
    {toolbar}
    {''.join(b for b in blocks if b)}
  </div>
""" + script_js + """
</body>
</html>
"""
    return html_doc


def main(argv):
    def usage_and_exit():
        print(
            "Usage:\n"
            "  render_jsonl.py <your_codex_log.jsonl> [-o output.html]\n"
            "  render_jsonl.py --all [--sessions-dir DIR]",
            file=sys.stderr,
        )
        sys.exit(1)

    if len(argv) < 2:
        usage_and_exit()

    # Batch mode: crawl ~/.codex/sessions and mirror outputs here
    if argv[1] == "--all":
        sessions_dir = None
        if len(argv) >= 4 and argv[2] == "--sessions-dir":
            sessions_dir = argv[3]
        if sessions_dir is None:
            sessions_dir = os.path.expanduser("~/.codex/sessions")

        sessions_root = Path(sessions_dir)
        if not sessions_root.exists():
            print(f"Error: sessions directory not found: {sessions_root}", file=sys.stderr)
            sys.exit(2)

        dest_root = Path.cwd()

        def parse_date_time_from_name(name: str):
            # Expect names like rollout-2025-09-09T20-09-59-<uuid>.jsonl
            m = re.search(r"(\d{4}-\d{2}-\d{2})T(\d{2}-\d{2}-\d{2})", name)
            if m:
                return m.group(1), m.group(2)
            return None, None

        index_items = []  # list of dicts with date, time, rel_html
        for src in sessions_root.rglob("*.jsonl"):
            rel = src.relative_to(sessions_root)
            out_html = (dest_root / rel).with_suffix(".html")
            out_html.parent.mkdir(parents=True, exist_ok=True)
            try:
                html_doc = render_jsonl_to_html(str(src))
                out_html.write_text(html_doc, encoding="utf-8")
            except Exception as e:
                print(f"Warning: failed to render {src}: {e}", file=sys.stderr)
                continue

            date_str, time_str = parse_date_time_from_name(src.name)
            if not date_str:
                # fallback to file modified time
                try:
                    ts = src.stat().st_mtime
                    import datetime as _dt

                    dt = _dt.datetime.fromtimestamp(ts)
                    date_str = dt.strftime("%Y-%m-%d")
                    time_str = dt.strftime("%H-%M-%S")
                except Exception:
                    date_str = "Unknown"
                    time_str = src.stem[:8]

            index_items.append({
                "date": date_str,
                "time": time_str or "",
                "rel_html": str(out_html.relative_to(dest_root)).replace(os.sep, "/"),
            })

        # Group by date and sort by date/time descending
        groups = defaultdict(list)
        for it in index_items:
            groups[it["date"]].append(it)
        def sort_key(it):
            return (it["time"], it["rel_html"])  # time sorts lexicographically HH-MM-SS
        for d in groups:
            groups[d].sort(key=sort_key, reverse=True)
        dates_sorted = sorted(groups.keys(), reverse=True)

        # Build index HTML
        index_blocks = [
            "<div class='session'><div class='title'>Codex Sessions</div><div class='subtitle'>Batch conversion from ~/.codex/sessions</div></div>"
        ]
        for d in dates_sorted:
            index_blocks.append(f"<h2>{html.escape(d)}</h2>")
            index_blocks.append("<ul>")
            for it in groups[d]:
                label = html.escape(it["time"]) if it["time"] else html.escape(Path(it["rel_html"]).name)
                href = html.escape(it["rel_html"]) 
                index_blocks.append(f"<li><a href='{href}'>{label}</a></li>")
            index_blocks.append("</ul>")

        index_doc = f"""
<!DOCTYPE html>
<html lang='en'>
<head>
  <meta charset='utf-8' />
  <meta name='viewport' content='width=device-width, initial-scale=1' />
  <title>Codex Sessions — Index</title>
  <style>{STYLE}</style>
</head>
<body>
  <div class='container'>
    {''.join(index_blocks)}
  </div>
</body>
</html>
"""
        (dest_root / "index.html").write_text(index_doc, encoding="utf-8")
        print(f"Converted {len(index_items)} files. Wrote index.html")
        return

    # Single-file mode
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
