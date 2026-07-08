from event_sync.wix_mapping import format_description_as_html, wix_timestamp


def test_format_description_preserves_paragraphs_and_line_breaks():
    raw = "First line\nSecond line\n\nThird paragraph"
    expected = "<p>First line<br/>Second line</p><p>Third paragraph</p>"

    assert format_description_as_html(raw) == expected


def test_format_description_converts_bullet_lists():
    raw = "- Item one\n- Item two\n- Item three"
    expected = "<ul><li>Item one</li><li>Item two</li><li>Item three</li></ul>"

    assert format_description_as_html(raw) == expected


def test_format_description_handles_unicode_bullets():
    raw = "\u2022 Item A\n\u2022 Item B"
    expected = "<ul><li>Item A</li><li>Item B</li></ul>"

    assert format_description_as_html(raw) == expected


def test_format_description_splits_text_and_bullets():
    """Bullet list gets its own <ul> even without blank lines around it."""
    raw = "Key lessons include:\n- Bullet one\n- Bullet two\nClosing paragraph."
    expected = (
        "<p>Key lessons include:</p>"
        "<ul><li>Bullet one</li><li>Bullet two</li></ul>"
        "<p>Closing paragraph.</p>"
    )
    assert format_description_as_html(raw) == expected


def test_format_description_preserves_inline_html_tags():
    raw = "This is <b>important</b> text"
    expected = "<p>This is <b>important</b> text</p>"

    assert format_description_as_html(raw) == expected


def test_format_description_preserves_br_tags():
    raw = "Line one<br>Line two"
    expected = "<p>Line one<br>Line two</p>"

    assert format_description_as_html(raw) == expected


def test_format_description_preserves_anchor_tags():
    raw = 'Click <a href="https://example.com">here</a> for info'
    expected = '<p>Click <a href="https://example.com">here</a> for info</p>'

    assert format_description_as_html(raw) == expected


def test_format_description_escapes_unsafe_tags():
    raw = "Hello <script>alert('xss')</script>"
    result = format_description_as_html(raw)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_format_description_full_html_passthrough():
    """Already fully-formed HTML is returned as-is (no double-wrapping)."""
    raw = "<p>Already formatted</p><ul><li>item</li></ul>"
    assert format_description_as_html(raw) == raw


def test_format_description_realistic_class_description():
    """Realistic description with intro, bullets, and closing."""
    raw = (
        "In this class, we will explore patterns.\n"
        "Key lessons include:\n"
        "- Using design to create opportunities\n"
        "- Building patterns that spread the load\n"
        "- Suspension demonstration\n"
        "This makes a powerful addition.\n"
        "Prerequisites: single column tie.\n"
        "Self Tie Friendly?: Yes"
    )
    result = format_description_as_html(raw)
    assert "<ul>" in result
    assert "<li>" in result
    assert result.count("<p>") >= 2
    # Bullets should be in a <ul>, not in a <p>
    assert "<p>- " not in result


def test_wix_timestamp_converts_to_utc_with_timezone():
    timestamp = wix_timestamp("2025-12-25", "07:00", "America/Toronto")

    assert timestamp == "2025-12-25T12:00:00Z"

