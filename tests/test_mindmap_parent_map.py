"""Tests for the mindmap parent map algorithm.

The algorithm lives in `app/templates/video.html` (JavaScript) as
`buildMindmapParentMap`. We port the same algorithm to Python here so
it can be unit-tested in isolation. The JS function MUST stay
behaviorally identical to this Python version — if you change the JS
algorithm, update this test to match.

The function takes a Markmap-compatible markdown tree and returns a
dict mapping each non-root node to its direct parent. Example:

    # RAG
    ## Overview
    ### Definition and Purpose
    ## Architecture
    ### Retriever
    ### Generator

becomes:

    {"Overview": "RAG", "Definition and Purpose": "Overview",
     "Architecture": "RAG", "Retriever": "Architecture",
     "Generator": "Architecture"}
"""

import re


def build_mindmap_parent_map(markdown: str) -> dict[str, str]:
    """Port of the JS buildMindmapParentMap for testing."""
    parent_map: dict[str, str] = {}
    if not markdown:
        return parent_map
    level_stack: dict[int, str] = {}
    for raw_line in markdown.split("\n"):
        line = raw_line.strip()
        # Match up to 4 leading # characters, then capture the rest as title.
        match = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        if level == 1:
            level_stack[1] = title
        else:
            parent = level_stack.get(level - 1)
            if parent and parent != title:
                parent_map[title] = parent
            level_stack[level] = title
            for d in range(level + 1, 5):
                level_stack.pop(d, None)
    return parent_map


def find_timestamp_with_ancestors(
    topic_name: str,
    parent_map: dict[str, str],
    topic_timestamps: list[dict],
) -> dict | None:
    """Port of the JS findTopicTimestampWithAncestors.

    Returns {'ts': <matched entry>, 'matched_name': <topic used>} or None.
    Match priority: exact → case-insensitive → partial (longest wins).
    Falls back to walking up the parent chain.
    """
    if not topic_name or not topic_timestamps:
        return None

    def _match(name: str) -> dict | None:
        # Exact
        for t in topic_timestamps:
            if t["topic"] == name:
                return t
        # Case-insensitive
        lower = name.lower()
        for t in topic_timestamps:
            if t["topic"].lower() == lower:
                return t
        # Partial — pick longest matching key
        candidates = [
            t for t in topic_timestamps
            if t["topic"].lower() in lower or lower in t["topic"].lower()
        ]
        if candidates:
            candidates.sort(key=lambda t: len(t["topic"]), reverse=True)
            return candidates[0]
        return None

    # Try the node itself first
    ts = _match(topic_name)
    if ts:
        return {"ts": ts, "matched_name": topic_name}
    # Then walk up the parent chain (with cycle protection)
    current = parent_map.get(topic_name)
    seen = {topic_name}
    while current and current not in seen:
        seen.add(current)
        ts = _match(current)
        if ts:
            return {"ts": ts, "matched_name": current}
        current = parent_map.get(current)
    return None


# ── build_mindmap_parent_map tests ──


def test_parent_map_simple():
    md = "# Root\n## Branch\n### Leaf"
    assert build_mindmap_parent_map(md) == {"Branch": "Root", "Leaf": "Branch"}


def test_parent_map_multiple_branches():
    md = "# Root\n## A\n### A1\n## B\n### B1"
    assert build_mindmap_parent_map(md) == {
        "A": "Root", "A1": "A",
        "B": "Root", "B1": "B",
    }


def test_parent_map_rag_video_example():
    """Realistic RAG video mindmap."""
    md = (
        "# RAG\n"
        "## Overview\n"
        "### Definition and Purpose\n"
        "### Why It Matters\n"
        "## Architecture\n"
        "### Retriever\n"
        "### Generator\n"
        "### Vector Database\n"
    )
    assert build_mindmap_parent_map(md) == {
        "Overview": "RAG",
        "Definition and Purpose": "Overview",
        "Why It Matters": "Overview",
        "Architecture": "RAG",
        "Retriever": "Architecture",
        "Generator": "Architecture",
        "Vector Database": "Architecture",
    }


def test_parent_map_deeply_nested():
    md = "# A\n## B\n### C\n#### D"
    assert build_mindmap_parent_map(md) == {
        "B": "A", "C": "B", "D": "C",
    }


def test_parent_map_empty():
    assert build_mindmap_parent_map("") == {}


def test_parent_map_no_headings():
    assert build_mindmap_parent_map("just some text\nno headings here") == {}


def test_parent_map_deeper_levels_invalidate_stack():
    """When a level-2 branch reappears after a level-3, the level-3 entries
    from the previous branch should NOT be treated as parents of the new
    level-2 branch's children."""
    md = (
        "# Root\n"
        "## A\n"
        "### OldLeaf\n"
        "## B\n"
        "### NewLeaf\n"
    )
    parent_map = build_mindmap_parent_map(md)
    assert parent_map["A"] == "Root"
    assert parent_map["OldLeaf"] == "A"
    assert parent_map["B"] == "Root"
    assert parent_map["NewLeaf"] == "B"
    # The critical check: NewLeaf should NOT have OldLeaf or A as parent
    assert parent_map["NewLeaf"] == "B"


# ── find_timestamp_with_ancestors tests ──


def test_match_exact():
    ts_list = [{"topic": "Overview", "start": 0, "end": 60}]
    result = find_timestamp_with_ancestors("Overview", {}, ts_list)
    assert result["matched_name"] == "Overview"
    assert result["ts"]["start"] == 0


def test_match_walks_up_to_parent():
    """The key test for the bug: leaf node has no exact match, but its
    parent does, so we should use the parent's timestamp."""
    parent_map = {"Definition and Purpose": "Overview"}
    ts_list = [{"topic": "Overview", "start": 0, "end": 120}]
    result = find_timestamp_with_ancestors("Definition and Purpose", parent_map, ts_list)
    assert result is not None
    assert result["matched_name"] == "Overview"
    assert result["ts"]["start"] == 0


def test_match_walks_multiple_levels():
    """3-level deep leaf, only root has a timestamp."""
    parent_map = {"B": "A", "C": "B"}
    ts_list = [{"topic": "A", "start": 0, "end": 60}]
    result = find_timestamp_with_ancestors("C", parent_map, ts_list)
    assert result is not None
    assert result["matched_name"] == "A"


def test_match_returns_none_when_no_ancestor_matches():
    parent_map = {"Leaf": "Branch"}
    ts_list = [{"topic": "OtherTopic", "start": 0, "end": 60}]
    result = find_timestamp_with_ancestors("Leaf", parent_map, ts_list)
    assert result is None


def test_match_case_insensitive():
    ts_list = [{"topic": "Overview", "start": 0, "end": 60}]
    result = find_timestamp_with_ancestors("overview", {}, ts_list)
    assert result is not None
    assert result["matched_name"] == "overview"


def test_match_partial_picks_longest():
    """When multiple partial matches exist, pick the most specific one."""
    ts_list = [
        {"topic": "RAG", "start": 0, "end": 60},
        {"topic": "RAG Architecture", "start": 60, "end": 120},
    ]
    result = find_timestamp_with_ancestors("RAG Architecture Details", {}, ts_list)
    assert result is not None
    # The user clicked "RAG Architecture Details" (not in timestamps), but
    # the partial match for "RAG Architecture" was selected because it is
    # the longest match. The matched_name is the *search key*, and the
    # returned ts uses the *timestamp key* "RAG Architecture".
    assert result["matched_name"] == "RAG Architecture Details"
    assert result["ts"]["topic"] == "RAG Architecture"
    assert result["ts"]["start"] == 60


def test_match_handles_empty_inputs():
    assert find_timestamp_with_ancestors("", {}, []) is None
    assert find_timestamp_with_ancestors("X", {}, []) is None
    assert find_timestamp_with_ancestors(None, {}, [{"topic": "X", "start": 0, "end": 1}]) is None


def test_match_avoids_infinite_loop_on_cycle():
    """If parentMap has a cycle (shouldn't happen, but safety first)."""
    parent_map = {"A": "B", "B": "A"}
    ts_list = [{"topic": "Other", "start": 0, "end": 60}]
    result = find_timestamp_with_ancestors("A", parent_map, ts_list)
    assert result is None  # No infinite loop, just None
