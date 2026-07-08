"""Minimal markdown → HTML helper, mirroring the `simpleMarkdown` JS
function in app/templates/video.html.

The two implementations MUST stay byte-identical for any given input
because:

1. Server-rendered HTML (from this function via the `md` Jinja filter)
   needs to match the client-rendered HTML produced after a successful
   `/api/generate/{id}/assets/summary` fetch — otherwise the DOM would
   visibly change shape on the first tab switch.
2. The byte-equality test in tests/test_frontend.py locks the contract.
   If you change one implementation, you must change the other and
   update the test fixtures in lockstep.

Supported syntax (intentionally minimal — same subset as the JS version):
  - # / ## / ###         → h1 / h2 / h3
  - **bold**             → <strong>
  - *italic*             → <em>
  - `code`               → <code>
  - - item               → <li class="list-disc">
  - 1. item              → <li class="list-decimal">
  - blank line           → <br><br>
  - other newlines       → <br>
"""

from __future__ import annotations

import re

# Match up to 3 leading # characters, then capture the rest of the line
# as the heading text. The `m` flag makes ^ match line starts, so each
# heading is converted independently.
_H3_RE = re.compile(r"^### (.*)$", re.MULTILINE)
_H2_RE = re.compile(r"^## (.*)$", re.MULTILINE)
_H1_RE = re.compile(r"^# (.*)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
_EM_RE = re.compile(r"\*(.*?)\*")
_CODE_RE = re.compile(r"`(.*?)`")
_UL_RE = re.compile(r"^- (.*)$", re.MULTILINE)
_OL_RE = re.compile(r"^\d+\. (.*)$", re.MULTILINE)

# The class strings MUST match the JS function's output exactly. They
# are part of the byte-equality contract.
_H3_CLASS = '<h3 class="text-base font-semibold mt-3 mb-1">'
_H2_CLASS = '<h2 class="text-lg font-semibold mt-4 mb-2">'
_H1_CLASS = '<h1 class="text-xl font-bold mt-4 mb-2">'
_CODE_CLASS = (
    '<code class="bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs">'
)


def simple_markdown(md: str) -> str:
    """Convert a minimal markdown subset to HTML.

    Returns "" for empty/None input. The function is pure — same input
    always produces the same output — and idempotent up to newline
    normalization.

    The replacement order matters: the heading regexes must run BEFORE
    the bold/em regexes, otherwise a line like `# **Title**` would have
    the `**` stripped before the heading marker is recognized. Same
    for the JS version.
    """
    if not md:
        return ""
    out = md
    out = _H3_RE.sub(_H3_CLASS + r"\1</h3>", out)
    out = _H2_RE.sub(_H2_CLASS + r"\1</h2>", out)
    out = _H1_RE.sub(_H1_CLASS + r"\1</h1>", out)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = _EM_RE.sub(r"<em>\1</em>", out)
    out = _CODE_RE.sub(_CODE_CLASS + r"\1</code>", out)
    out = _UL_RE.sub(r'<li class="ml-4 list-disc">\1</li>', out)
    out = _OL_RE.sub(r'<li class="ml-4 list-decimal">\1</li>', out)
    out = out.replace("\n\n", "<br><br>").replace("\n", "<br>")
    return out
