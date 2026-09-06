import datetime as dt

from shortforge.sources.base import Item
from shortforge.writer import grounding, template
from shortforge.writer.script import build_description, build_title, split_sentences


def test_split_sentences_handles_abbreviations_and_initials():
    text = "Dr. Smith arrived in the U.S. in 1901. He met J. R. Jones at St. Paul's. It rained! Really? Yes."
    out = split_sentences(text)
    assert out == [
        "Dr. Smith arrived in the U.S. in 1901.",
        "He met J. R. Jones at St. Paul's.",
        "It rained!",
        "Really?",
        "Yes.",
    ]


def test_split_sentences_quotes_and_numbers():
    out = split_sentences('He said "no." 1620 was the year. (It was.) Done.')
    assert out[0] == 'He said "no."'
    assert out[1] == "1620 was the year."
    assert out[2] == "(It was.)"


def test_grounding_accepts_source_facts(item):
    v = grounding.check(["On this day in 1620.", "After 10 weeks at sea, Mayflower reached Cape Cod with 102 passengers."], item)
    assert v.ok, v.reason()


def test_grounding_rejects_invented_number(item):
    v = grounding.check(["The Mayflower carried 150 passengers."], item)
    assert not v.ok
    assert v.bad_numbers == ["150"]


def test_grounding_rejects_invented_name(item):
    v = grounding.check(["Captain Christopher Jones steered the Mayflower."], item)
    assert not v.ok
    assert "Christopher" in v.bad_names and "Jones" in v.bad_names


def test_grounding_allows_years_ago_derivation(item):
    today = dt.date(2026, 9, 6)
    v = grounding.check(["That was 406 years ago."], item, today=today)
    assert v.ok, v.reason()
    v2 = grounding.check(["That was 407 years ago."], item, today=today)
    assert not v2.ok


def test_grounding_number_words(item):
    assert grounding.check(["Ten weeks at sea."], item).ok
    assert not grounding.check(["Twelve weeks at sea."], item).ok
    # "one" is not treated as a number ("one true story")
    assert grounding.check(["One ship, one voyage."], item).ok


def test_grounding_allows_possessive_and_plural(item):
    assert grounding.check(["The Pilgrims' ship.", "Mayflower's crew."], item).ok


def test_template_writer_is_verbatim_and_bounded(item):
    s = template.write(item, target_seconds=45, channel_name="Today in History")
    assert s.hook == "On this day in 1620."
    for sent in s.sentences:
        assert sent in item.source_text
    assert s.writer == "template"
    assert s.title.endswith("#Shorts") and len(s.title) <= 100
    assert "Source: Wikipedia" in s.description
    assert "synthetic" in s.description.lower()
    assert grounding.check([s.hook, *s.sentences], item).ok


def test_template_shorten_drops_last_sentence(item):
    s = template.write(item, 45, "x")
    n = len(s.sentences)
    s2 = template.shorten(s)
    assert len(s2.sentences) == n - 1
    cur = s2
    while cur is not None:
        assert len(cur.sentences) >= 1
        last = cur
        cur = template.shorten(cur)
    assert len(last.sentences) == 1


def test_title_truncation():
    it = Item(id="x", pack="onthisday", title="A" * 200, source_text="", source_url="", source_label="Wikipedia", year=1999, date_label="January 1")
    t = build_title(it, "")
    assert len(t) <= 100 and t.endswith("#Shorts")


def test_description_includes_credit_and_sources(item):
    from shortforge.sources.base import Image

    item.image = Image(url="u", license="cc-by-sa-4.0", credit="Image: Someone / Wikimedia Commons (CC BY-SA 4.0)", page_url="https://commons.wikimedia.org/wiki/File:X.jpg")
    d = build_description(item, ["a.", "b."], "Chan")
    assert "CC BY-SA 4.0" in d and "commons.wikimedia.org" in d and item.source_url in d
    assert len(d) <= 4900
