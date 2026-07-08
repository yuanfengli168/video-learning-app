"""Unit tests for app/services/markdown.py — `simple_markdown`.

Aims for 100% branch/line coverage. The byte-equality contract with the
JS `simpleMarkdown` function is enforced in
`tests/test_frontend.py::test_simple_markdown_matches_js_implementation` —
this file is for hitting the few corner cases that the battery in that
test doesn't reach (e.g. empty input, lines that don't match any
regex, mixed CRLF).
"""

import pytest

from app.services.markdown import simple_markdown


def test_empty_input_returns_empty_string():
    assert simple_markdown("") == ""
    assert simple_markdown(None) == ""  # type: ignore[arg-type]


def test_plain_text_passes_through():
    # No markdown syntax → output is the input with newlines → <br>.
    assert simple_markdown("hello world") == "hello world"


def test_single_newline_becomes_single_br():
    """A single \\n is replaced with a single <br>."""
    assert simple_markdown("a\nb") == "a<br>b"


def test_double_newline_becomes_double_br():
    """\\n\\n (paragraph break) becomes <br><br>."""
    out = simple_markdown("para 1\n\npara 2")
    assert "<br><br>" in out
    # Order matters: the \\n\\n replacement runs first, so the result
    # is "para 1<br><br>para 2" (the second <br> from the single-\\n
    # pass replaces the \n in the original).
    assert out == "para 1<br><br>para 2"


def test_h1_h2_h3_headings():
    assert simple_markdown("# H1") == (
        '<h1 class="text-xl font-bold mt-4 mb-2">H1</h1>'
    )
    assert simple_markdown("## H2") == (
        '<h2 class="text-lg font-semibold mt-4 mb-2">H2</h2>'
    )
    assert simple_markdown("### H3") == (
        '<h3 class="text-base font-semibold mt-3 mb-1">H3</h3>'
    )


def test_bold_and_italic_and_code():
    assert simple_markdown("**bold**") == "<strong>bold</strong>"
    assert simple_markdown("*em*") == "<em>em</em>"
    assert simple_markdown("`code`") == (
        '<code class="bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs">code</code>'
    )


def test_unordered_and_ordered_lists():
    assert simple_markdown("- item") == '<li class="ml-4 list-disc">item</li>'
    assert simple_markdown("1. first") == '<li class="ml-4 list-decimal">first</li>'
    assert simple_markdown("10. tenth") == '<li class="ml-4 list-decimal">tenth</li>'


def test_heading_regex_runs_before_bold():
    """If we ran bold/em first, `# **Title**` would have the `**` stripped
    before the heading marker was recognized. The function documents
    this ordering constraint; lock it in with a test.
    """
    out = simple_markdown("# **Title**")
    assert out.startswith('<h1 class="text-xl font-bold mt-4 mb-2">')
    assert "<strong>Title</strong>" in out


def test_multiline_input():
    """Real-world summary from Ollama is multi-line. Exercise a longer
    sample to catch newline-handling regressions."""
    src = (
        "# Topic\n"
        "\n"
        "Some intro text.\n"
        "\n"
        "## Subtopic\n"
        "\n"
        "- bullet one\n"
        "- bullet two\n"
        "\n"
        "Code: `x = 1`\n"
    )
    out = simple_markdown(src)
    # All major syntaxes appear.
    assert '<h1 class="text-xl font-bold mt-4 mb-2">Topic</h1>' in out
    assert '<h2 class="text-lg font-semibold mt-4 mb-2">Subtopic</h2>' in out
    assert '<li class="ml-4 list-disc">bullet one</li>' in out
    assert '<li class="ml-4 list-disc">bullet two</li>' in out
    assert '<code class="bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs">x = 1</code>' in out
    # Paragraphs were joined by <br><br>.
    assert "<br><br>" in out


@pytest.mark.parametrize(
    "src,expected_fragment",
    [
        ("## Only heading", '<h2 class="text-lg font-semibold mt-4 mb-2">Only heading</h2>'),
        ("**only bold**", "<strong>only bold</strong>"),
        ("*only italic*", "<em>only italic</em>"),
        ("`only code`", '<code class="bg-gray-100 dark:bg-gray-700 px-1 rounded text-xs">only code</code>'),
    ],
)
def test_solo_syntaxes(src: str, expected_fragment: str):
    """Each syntax type in isolation."""
    assert simple_markdown(src) == expected_fragment


def test_no_unintended_replacement_for_non_matching_lines():
    """Lines that match no syntax must pass through (with \\n → <br>)."""
    src = "plain line\nanother line"
    assert simple_markdown(src) == "plain line<br>another line"
