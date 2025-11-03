from event_sync.orchestrator import format_description_as_html, _wix_timestamp


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


def test_wix_timestamp_converts_to_utc_with_timezone():
    timestamp = _wix_timestamp("2025-12-25", "07:00", "America/Toronto")

    assert timestamp == "2025-12-25T12:00:00Z"

