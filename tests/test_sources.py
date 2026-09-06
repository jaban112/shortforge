import datetime as dt

from shortforge.sources import Apod, OnThisDay, license_allowed
from shortforge.sources.onthisday import commons_filename, commons_license


def test_license_gate():
    assert license_allowed("cc-by-sa-4.0")
    assert license_allowed("CC BY 2.0")
    assert license_allowed("cc0")
    assert license_allowed("pd")
    assert license_allowed("Public domain")
    assert license_allowed("pd-us")
    assert not license_allowed("cc-by-nc-sa-2.0")
    assert not license_allowed("cc-by-nd-4.0")
    assert not license_allowed("fair use")
    assert not license_allowed("")
    assert not license_allowed(None)
    assert not license_allowed("copyrighted")


def test_commons_filename():
    assert commons_filename("https://upload.wikimedia.org/wikipedia/commons/2/2b/Mayflower_in_Plymouth_Harbor.jpg") == "File:Mayflower in Plymouth Harbor.jpg"
    assert commons_filename("https://upload.wikimedia.org/wikipedia/commons/thumb/2/2b/Foo%27s_Bar.jpg/320px-Foo%27s_Bar.jpg") == "File:Foo's Bar.jpg"
    assert commons_filename("https://upload.wikimedia.org/wikipedia/en/3/3a/Nonfree.png") is None


def _commons_resp(license_code, short, artist="<a href='x'>Jane Doe</a>", missing=False):
    page = {"title": "File:X.jpg"}
    if missing:
        page["missing"] = ""
    else:
        page["imageinfo"] = [{
            "url": "https://upload.wikimedia.org/x.jpg",
            "thumburl": "https://upload.wikimedia.org/thumb/x.jpg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:X.jpg",
            "extmetadata": {"License": {"value": license_code}, "LicenseShortName": {"value": short}, "Artist": {"value": artist}},
        }]
    return {"query": {"pages": {"1": page}}}


def test_commons_license_accepts_cc_by_sa(fake_http):
    http = fake_http({"commons.wikimedia.org": _commons_resp("cc-by-sa-4.0", "CC BY-SA 4.0")})
    img = commons_license(http, "File:X.jpg")
    assert img is not None
    assert img.url.endswith("thumb/x.jpg")
    assert img.credit == "Image: Jane Doe / Wikimedia Commons (CC BY-SA 4.0)"
    assert img.page_url == "https://commons.wikimedia.org/wiki/File:X.jpg"


def test_commons_license_rejects_nc_and_missing(fake_http):
    assert commons_license(fake_http({"commons": _commons_resp("cc-by-nc-2.0", "CC BY-NC 2.0")}), "File:X.jpg") is None
    assert commons_license(fake_http({"commons": _commons_resp("", "", missing=True)}), "File:X.jpg") is None


def test_onthisday_builds_items_and_orders_images_first(fake_http):
    feed = {"selected": [
        {"text": "Event without image happened.", "year": 1900, "pages": [{"title": "NoImg", "titles": {"normalized": "NoImg"}, "extract": "Long extract here. " * 5, "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/NoImg"}}}]},
        {"text": "Event with image happened.", "year": 1950, "pages": [{"title": "Img", "titles": {"normalized": "Img"}, "extract": "Short.", "originalimage": {"source": "https://upload.wikimedia.org/wikipedia/commons/1/1a/Img.jpg"}, "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Img"}}}]},
    ]}
    http = fake_http({"onthisday": feed, "commons.wikimedia.org": _commons_resp("pd", "Public domain", artist="NASA")})

    class Cfg:
        language = "en"
        user_agent = "t"

    items = OnThisDay().fetch(http, Cfg(), dt.date(2026, 9, 6))
    assert [i.title for i in items] == ["Img", "NoImg"]
    assert items[0].image is not None and items[0].image.license == "pd"
    assert items[0].id.startswith("otd:09-06:1950:")
    assert items[0].date_label == "September 6"
    assert items[1].image is None
    assert "Event without image happened." in items[1].source_text
    assert items[1].extra["event_text"] == "Event without image happened."


def test_apod_filters_copyright_and_video(fake_http):
    entries = [
        {"date": "2024-01-01", "media_type": "image", "title": "A", "explanation": "x" * 50, "hdurl": "https://apod.nasa.gov/a.jpg"},
        {"date": "2024-01-02", "media_type": "image", "title": "B", "explanation": "y", "url": "https://apod.nasa.gov/b.jpg", "copyright": "Someone"},
        {"date": "2024-01-03", "media_type": "video", "title": "C", "explanation": "z", "url": "https://youtube.com/x"},
    ]
    http = fake_http({"\"date\"": entries[0], "\"count\"": entries})

    class Cfg:
        nasa_api_key = "DEMO_KEY"

    items = Apod().fetch(http, Cfg(), dt.date(2026, 9, 6))
    ids = {i.id for i in items}
    assert ids == {"apod:2024-01-01"}
    it = items[0]
    assert it.image.license == "pd-nasa"
    assert it.source_url == "https://apod.nasa.gov/apod/ap240101.html"
