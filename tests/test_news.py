"""Tests for RSS news ingestion (nse/news/) -- the first piece of Phase 4.

Point-in-time discipline is the thing that matters most here: as_of() must
never return an article published at or after the query time, matching the
same contract as nse/pit/store.py.
"""

from datetime import datetime, timedelta, timezone

import pytest

from nse.news import rss_ingest
from nse.news.store import NewsItem, NewsStore

UTC = timezone.utc


def _item(url="https://x/1", symbols=("RELIANCE",), published=None, **kw):
    published = published or datetime(2026, 9, 1, tzinfo=UTC)
    defaults = dict(
        source="test", url=url, headline="headline", summary="summary",
        published_at=published, fetched_at=published, symbols=symbols,
    )
    defaults.update(kw)
    return NewsItem(**defaults)


# ------------------------------------------------------------------- store
def test_naive_timestamp_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        NewsItem(source="s", url="u", headline="h", summary="s",
                 published_at=datetime(2026, 9, 1), fetched_at=datetime.now(UTC))


def test_put_and_as_of_respects_point_in_time():
    with NewsStore() as store:
        store.put([_item(published=datetime(2026, 9, 1, 10, 0, tzinfo=UTC))])
        # exactly at published_at: not yet visible (strictly earlier required)
        assert store.as_of("RELIANCE", datetime(2026, 9, 1, 10, 0, tzinfo=UTC)) == []
        # one second later: visible
        rows = store.as_of("RELIANCE", datetime(2026, 9, 1, 10, 0, 1, tzinfo=UTC))
        assert len(rows) == 1
        assert rows[0]["url"] == "https://x/1"


def test_as_of_filters_by_symbol():
    with NewsStore() as store:
        store.put([_item(url="https://x/1", symbols=("RELIANCE",)),
                   _item(url="https://x/2", symbols=("TCS",))])
        later = datetime(2026, 9, 2, tzinfo=UTC)
        assert [r["url"] for r in store.as_of("RELIANCE", later)] == ["https://x/1"]
        assert [r["url"] for r in store.as_of("TCS", later)] == ["https://x/2"]
        assert store.as_of("INFY", later) == []


def test_put_dedupes_by_url():
    with NewsStore() as store:
        added1 = store.put([_item(url="https://x/1")])
        added2 = store.put([_item(url="https://x/1")])  # same URL again
        assert added1 == 1
        assert added2 == 0
        assert store.count() == 1


def test_as_of_orders_most_recent_first():
    with NewsStore() as store:
        store.put([
            _item(url="https://x/old", published=datetime(2026, 9, 1, tzinfo=UTC)),
            _item(url="https://x/new", published=datetime(2026, 9, 3, tzinfo=UTC)),
        ])
        rows = store.as_of("RELIANCE", datetime(2026, 9, 5, tzinfo=UTC))
        assert [r["url"] for r in rows] == ["https://x/new", "https://x/old"]


def test_estimated_flag_is_persisted():
    with NewsStore() as store:
        store.put([_item(published_at_estimated=True)])
        rows = store.as_of("RELIANCE", datetime(2026, 9, 5, tzinfo=UTC))
        assert rows[0]["published_at_estimated"] == 1


# --------------------------------------------------------------- link_symbols
def test_link_symbols_matches_whole_word_ticker():
    universe = ["RELIANCE", "TCS", "INFY"]
    assert rss_ingest.link_symbols("RELIANCE surges on strong results", universe) == ("RELIANCE",)


def test_link_symbols_no_match_on_substring():
    # "TCS" should not match inside an unrelated longer word
    universe = ["TCS"]
    assert rss_ingest.link_symbols("The METCS report was delayed", universe) == ()


def test_link_symbols_multiple_hits():
    universe = ["RELIANCE", "TCS"]
    text = "RELIANCE and TCS both rose in early trade"
    assert set(rss_ingest.link_symbols(text, universe)) == {"RELIANCE", "TCS"}


def test_link_symbols_skips_ambiguous_guard_list():
    assert rss_ingest.link_symbols("IEX prices jumped today", ["IEX"]) == ()


# ---------------------------------------------------------------- fetch_all
_SAMPLE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test Feed</title>
<item>
  <title>RELIANCE Q2 results beat estimates</title>
  <link>https://example.com/a</link>
  <description>&lt;p&gt;Reliance Industries posted strong earnings this quarter.&lt;/p&gt;</description>
  <pubDate>Mon, 01 Sep 2026 07:00:00 +0000</pubDate>
</item>
<item>
  <title>Market wrap: Sensex, Nifty close flat</title>
  <link>https://example.com/b</link>
  <description>No specific stock mentioned here.</description>
  <pubDate>Mon, 01 Sep 2026 16:00:00 +0000</pubDate>
</item>
<item>
  <title>Undated wire update</title>
  <link>https://example.com/c</link>
  <description>No pubDate on this entry.</description>
</item>
</channel></rss>
"""


def test_fetch_all_parses_dates_links_symbols_and_truncates_summary():
    items = rss_ingest.fetch_all(["RELIANCE", "TCS"], feeds={"test": _SAMPLE_RSS})
    by_url = {it.url: it for it in items}

    a = by_url["https://example.com/a"]
    assert a.symbols == ("RELIANCE",)
    assert a.published_at == datetime(2026, 9, 1, 7, 0, tzinfo=UTC)
    assert a.published_at_estimated is False
    assert "<p>" not in a.summary  # HTML stripped
    assert "Reliance Industries posted strong earnings" in a.summary

    b = by_url["https://example.com/b"]
    assert b.symbols == ()


def test_fetch_all_falls_back_to_fetched_at_when_pubdate_missing():
    items = rss_ingest.fetch_all(["RELIANCE"], feeds={"test": _SAMPLE_RSS})
    c = next(it for it in items if it.url == "https://example.com/c")
    assert c.published_at_estimated is True
    assert c.published_at == c.fetched_at


def test_fetch_all_truncates_long_summaries():
    long_desc = "x" * 5000
    xml = f"""<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <title>Long article</title>
      <link>https://example.com/long</link>
      <description>{long_desc}</description>
      <pubDate>Mon, 01 Sep 2026 07:00:00 +0000</pubDate>
    </item></channel></rss>"""
    items = rss_ingest.fetch_all([], feeds={"test": xml})
    assert len(items[0].summary) <= rss_ingest.MAX_SUMMARY_CHARS


def test_fetch_all_skips_entries_missing_link_or_title():
    xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
    <item><title>No link here</title><description>x</description>
      <pubDate>Mon, 01 Sep 2026 07:00:00 +0000</pubDate></item>
    <item><link>https://example.com/notitle</link><description>x</description>
      <pubDate>Mon, 01 Sep 2026 07:00:00 +0000</pubDate></item>
    </channel></rss>"""
    items = rss_ingest.fetch_all([], feeds={"test": xml})
    assert items == []


def test_fetch_all_one_broken_feed_does_not_block_others(monkeypatch):
    real_parse = rss_ingest.feedparser.parse

    def fake_parse(source):
        if source == "bad":
            raise RuntimeError("network exploded")
        return real_parse(source)

    monkeypatch.setattr(rss_ingest.feedparser, "parse", fake_parse)
    items = rss_ingest.fetch_all(["RELIANCE"], feeds={"bad": "bad", "good": _SAMPLE_RSS})
    assert len(items) == 3  # only the good feed's entries came through
