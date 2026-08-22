"""Tests for Video model."""

from app.models import Asset, Course, Section, Video
from app.models.video import natural_sort_key, natural_sort_key_str


def test_create_video(db_session):
    """Should create a video with default fields."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    video = Video(
        title="Intro to NN",
        filename="intro.mp4",
        file_path="/uploads/intro.mp4",
        file_size=1024,
        section_id=section.id,
    )
    db_session.add(video)
    db_session.commit()

    assert video.id is not None
    assert video.title == "Intro to NN"
    assert video.status == "pending"
    # MVP3.0 #2: whisper_model is no longer a hard default of "base"
    # on the column — it's set explicitly when the user picks a
    # model choice. A freshly-created Video row has whisper_model
    # = None until the transcribe endpoint stores the chosen key.
    assert video.whisper_model is None
    # The new MVP3.0 #2 columns (whisper_backend, resolved_model,
    # fallback_reason) are also None at creation time.
    assert video.whisper_backend is None
    assert video.whisper_resolved_model is None
    assert video.whisper_fallback_reason is None
    assert video.file_size == 1024
    assert video.duration == 0


def test_video_asset_relationship(db_session):
    """Video should have an assets relationship."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    video = Video(title="Intro", filename="intro.mp4", file_path="/tmp/intro.mp4",
                  section_id=section.id)
    db_session.add(video)
    db_session.flush()

    asset = Asset(video_id=video.id, asset_type="summary", content="# Summary")
    db_session.add(asset)
    db_session.commit()

    assert len(video.assets) == 1
    assert video.assets[0].asset_type == "summary"


def test_video_cascade_delete(db_session):
    """Deleting a video should cascade delete its assets."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    video = Video(title="Intro", filename="intro.mp4", file_path="/tmp/intro.mp4",
                  section_id=section.id)
    db_session.add(video)
    db_session.flush()

    asset = Asset(video_id=video.id, asset_type="summary", content="content")
    db_session.add(asset)
    db_session.commit()
    asset_id = asset.id

    db_session.delete(video)
    db_session.commit()

    assert db_session.get(Asset, asset_id) is None


def test_video_status_values(db_session):
    """Video status should accept various processing states."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()

    section = Section(title="Week 1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    for status in ["pending", "transcribing", "generating", "ready", "error"]:
        video = Video(title=f"V-{status}", filename="v.mp4", file_path="/tmp/v.mp4",
                      section_id=section.id, status=status)
        db_session.add(video)

    db_session.commit()
    videos = db_session.query(Video).all()
    statuses = {v.status for v in videos}
    assert "pending" in statuses
    assert "ready" in statuses
    assert "error" in statuses


# ── natural_sort_key tests (MVP2.0 #4) ────────────────────────────────────
# Used by the course page (app/templates/course.html) to sort videos
# by leading number in the title. The same logic is re-implemented
# in JS for the client-side sort, so these tests are the contract
# that the two implementations must agree on.


def test_natural_sort_key_leading_number_dot():
    """'1.-foo' should extract 1 as the leading number."""
    n, t = natural_sort_key("1.-AI大模型...")
    assert n == 1
    assert t == "1.-ai大模型..."


def test_natural_sort_key_leading_number_space():
    """'10 - bar' should extract 10 as the leading number."""
    n, t = natural_sort_key("10 - bar")
    assert n == 10
    assert t == "10 - bar"


def test_natural_sort_key_leading_number_underscore():
    """'2_机器学习' should extract 2 as the leading number."""
    n, t = natural_sort_key("2_机器学习")
    assert n == 2


def test_natural_sort_key_no_leading_number():
    """'Lesson 3: intro' has no leading number — number is the sentinel."""
    n, t = natural_sort_key("Lesson 3: intro")
    # Sentinel is 10**9 (a large int, not None, so the tuple is fully sortable)
    assert n == 10**9
    assert t == "lesson 3: intro"


def test_natural_sort_key_empty_string():
    """Empty title should not crash."""
    n, t = natural_sort_key("")
    # Empty title is treated as unnumbered, so it gets the 10**9 sentinel
    assert n == 10**9
    assert t == ""


def test_natural_sort_orders_correctly():
    """The main use case: 10 must sort after 1, not before."""
    titles = ["10.-bar", "1.-foo", "2.-baz", "ep5.mkv", "Lesson 3: intro"]
    sorted_titles = sorted(titles, key=natural_sort_key)
    # Expected order: 1, 2, 10, then the un-numbered ones
    assert sorted_titles[0] == "1.-foo"
    assert sorted_titles[1] == "2.-baz"
    assert sorted_titles[2] == "10.-bar"
    # The un-numbered ones come last, in alphabetic order
    assert sorted_titles[3] in ("Lesson 3: intro", "ep5.mkv")
    assert sorted_titles[4] in ("Lesson 3: intro", "ep5.mkv")


def test_natural_sort_key_str_pads_to_nine_digits():
    """The string form must be zero-padded so '1' < '10' lexically.
    This is the format the JS sort uses."""
    assert natural_sort_key_str("1.-foo") == "000000001:1.-foo"
    assert natural_sort_key_str("10.-bar") == "000000010:10.-bar"


def test_natural_sort_key_str_unnumbered_gets_sentinel():
    """Videos without a leading number get the 10**9 sentinel (padded
    to 9 digits as 1000000000) so they sort LAST in ascending order,
    FIRST in descending."""
    assert natural_sort_key_str("ep5.mkv") == "1000000000:ep5.mkv"
    assert natural_sort_key_str("Lesson 3") == "1000000000:lesson 3"


def test_natural_sort_key_str_lex_order_matches_natural():
    """The string form must produce the same order as natural_sort_key
    when compared lexicographically — that's the whole point of
    emitting it as data-sort-key for JS localeCompare."""
    titles = ["10.-bar", "1.-foo", "2.-baz", "ep5.mkv", "Lesson 3"]
    # Sort by tuple key (canonical natural order)
    natural = sorted(titles, key=natural_sort_key)
    # Sort by string key (what JS does)
    by_string = sorted(titles, key=natural_sort_key_str)
    assert natural == by_string


def test_natural_sort_key_real_data_from_user_test():
    """The 4-video bulk upload from july 9 2026 — verify they sort
    to 1, 2, 3, 4 (the order the user wants) and not 1, 4, 3, 2
    (the current arbitrary insertion order)."""
    titles = [
        "1.-AI大模型提示词工程深入实战-提示词工程课程介绍-09-07-2026",
        "2.-AI大模型提示词工程深入实战-prompt和Prompt engineering剖析-09-07-2026",
        "3.-AI大模型提示词工程深入实战-大模型介绍,生成简历,写小红书文案,生成图片-09-07-2026",
        "4.-AI大模型提示词工程深入实战-提示词基本技巧_灵活运营指令符号_指定输出格式markdown结合xmind生成思维导图-09-07-2026",
    ]
    sorted_titles = sorted(titles, key=natural_sort_key_str)
    assert sorted_titles[0].startswith("1.-")
    assert sorted_titles[1].startswith("2.-")
    assert sorted_titles[2].startswith("3.-")
    assert sorted_titles[3].startswith("4.-")

# ── MVP2 visibility column tests ──────────────────────────────────────────
# These cover the new videos.visibility column added in the Day 1
# migration. Visibility is an int matching VideoVisibility enum
# (0=PUBLIC, 1=PAID_ONLY, 2=ADMIN_ONLY). Default 0 (PUBLIC) ensures
# all existing rows stay visible to all users.


def test_video_default_visibility_is_public(db_session):
    """Brand-new videos default to PUBLIC so admin doesn't need to set it."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="S1", course_id=course.id)
    db_session.add(section)
    db_session.flush()
    video = Video(
        title="New upload",
        filename="x.mp4",
        file_path="/uploads/x.mp4",
        section_id=section.id,
    )
    db_session.add(video)
    db_session.commit()

    assert video.visibility == 0  # PUBLIC


def test_video_explicit_visibility_values(db_session):
    """Visibility accepts 0, 1, 2 (matching VideoVisibility enum)."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="S1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    videos = []
    for v_val in (0, 1, 2):
        v = Video(
            title=f"video-{v_val}",
            filename=f"f{v_val}.mp4",
            file_path=f"/uploads/f{v_val}.mp4",
            section_id=section.id,
            visibility=v_val,
        )
        videos.append(v)
    db_session.add_all(videos)
    db_session.commit()

    rows = (
        db_session.query(Video)
        .order_by(Video.visibility)
        .all()
    )
    assert [r.visibility for r in rows] == [0, 1, 2]


def test_video_visibility_backfills_to_public(db_session):
    """A row created without explicit visibility should be PUBLIC.

    This simulates the additive migration: existing rows that were
    inserted before the visibility column existed should default to
    PUBLIC (= 0) when the column is added.
    """
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="S1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    # Insert WITHOUT setting visibility, mimicking pre-migration row
    video = Video(
        title="Legacy video",
        filename="legacy.mp4",
        file_path="/uploads/legacy.mp4",
        section_id=section.id,
    )
    db_session.add(video)
    db_session.commit()

    # Force-refresh from DB so we see the column default, not Python-side
    db_session.expire(video)
    fresh = db_session.query(Video).filter_by(id=video.id).one()
    assert fresh.visibility == 0  # PUBLIC backfill


def test_video_query_filter_by_visibility(db_session):
    """A common query: list only PUBLIC videos (catalog page for FREE user)."""
    course = Course(title="ML", user_id="u1")
    db_session.add(course)
    db_session.flush()
    section = Section(title="S1", course_id=course.id)
    db_session.add(section)
    db_session.flush()

    public_v = Video(
        title="free", filename="a.mp4", file_path="/a",
        section_id=section.id, visibility=0,
    )
    paid_v = Video(
        title="paid", filename="b.mp4", file_path="/b",
        section_id=section.id, visibility=1,
    )
    admin_v = Video(
        title="admin", filename="c.mp4", file_path="/c",
        section_id=section.id, visibility=2,
    )
    db_session.add_all([public_v, paid_v, admin_v])
    db_session.commit()

    # FREE user query (only PUBLIC)
    visible_to_free = db_session.query(Video).filter(
        Video.visibility <= 0
    ).all()
    assert len(visible_to_free) == 1
    assert visible_to_free[0].title == "free"

    # PAID user query (PUBLIC + PAID_ONLY)
    visible_to_paid = db_session.query(Video).filter(
        Video.visibility <= 1
    ).all()
    assert len(visible_to_paid) == 2
    titles = sorted(v.title for v in visible_to_paid)
    assert titles == ["free", "paid"]

    # ADMIN query (everything)
    visible_to_admin = db_session.query(Video).filter(
        Video.visibility <= 2
    ).all()
    assert len(visible_to_admin) == 3
