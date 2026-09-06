"""YouTube upload through YouTube Studio's web UI (no API key, no Google Cloud project).

Selectors come from the stable ids YouTube Studio has used for years (#title-textarea,
#next-button, #done-button, tp-yt-paper-radio-button[name=...]) with text fallbacks in
English and Korean. UI automation is inherently brittle: every step is wrapped so that
on failure a screenshot lands in work/ui/ with the step name, and the error names the step.

This module has NOT been exercised against the live Studio from the build container
(no egress to youtube.com). It was exercised against a local look-alike page that
reproduces the same ids/structure; expect to send the first failure screenshot back.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

UPLOAD_URL = "https://www.youtube.com/upload"
STUDIO = "https://studio.youtube.com"
TXT_NEXT = re.compile(r"^(Next|다음)$")
TXT_DONE = re.compile(r"^(Done|Publish|완료|게시)$")
TXT_YES = re.compile(r"^(Yes|예)")
TXT_NOT_KIDS = re.compile(r"(No, it's not made for kids|아니요, 아동용이 아닙니다)")


class UiStep(RuntimeError):
    def __init__(self, step: str, shot: Path | None, cause: Exception | str):
        super().__init__(f"youtube-web step '{step}' failed: {cause}" + (f" (screenshot {shot})" if shot else ""))
        self.step, self.shot = step, shot


class YouTubeWeb:
    def __init__(self, page, shots_dir: Path, log=print, timeout_ms: int = 60_000):
        self.page = page
        self.shots = Path(shots_dir)
        self.shots.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.t = timeout_ms

    # ---- helpers ----
    def _shot(self, name: str) -> Path | None:
        try:
            p = self.shots / f"yt-{int(time.time())}-{name}.png"
            self.page.screenshot(path=str(p), full_page=False)
            return p
        except Exception:
            return None

    def _step(self, name: str, fn):
        try:
            return fn()
        except Exception as e:
            raise UiStep(name, self._shot(name), e) from e

    def logged_in(self) -> bool:
        """True if Studio loads without redirecting to accounts.google.com."""
        self.page.goto(STUDIO, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        return "accounts.google.com" not in self.page.url

    # ---- upload ----
    def upload(self, mp4: Path, title: str, description: str, privacy: str = "public", synthetic: bool = True) -> str:
        p = self.page
        self.log(f"[yt-web] opening upload dialog")
        self._step("open", lambda: p.goto(UPLOAD_URL, wait_until="domcontentloaded"))
        if "accounts.google.com" in p.url:
            raise UiStep("login", self._shot("login"), "not signed in — press 'YouTube 로그인' first")

        def pick_file():
            inp = p.locator("input[type=file]").first
            inp.wait_for(state="attached", timeout=self.t)
            inp.set_input_files(str(mp4))
        self._step("file", pick_file)

        def fill_title():
            box = p.locator("#title-textarea #textbox, ytcp-social-suggestions-textbox#title-textarea [contenteditable]").first
            box.wait_for(state="visible", timeout=self.t)
            box.click()
            p.keyboard.press("Control+A")
            p.keyboard.type(title[:100], delay=5)
        self._step("title", fill_title)

        def fill_desc():
            box = p.locator("#description-textarea #textbox, ytcp-social-suggestions-textbox#description-textarea [contenteditable]").first
            box.wait_for(state="visible", timeout=self.t)
            box.click()
            p.keyboard.press("Control+A")
            p.keyboard.type(description[:5000], delay=1)
        self._step("description", fill_desc)

        def not_for_kids():
            r = p.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]')
            if r.count():
                r.first.click()
            else:
                p.get_by_text(TXT_NOT_KIDS).first.click()
        self._step("not_for_kids", not_for_kids)

        if synthetic:
            def altered():
                # "Altered content" section (2024+) lives under "Show more" — expand when the radio is not visible.
                sel = 'tp-yt-paper-radio-button[name="VIDEO_HAS_ALTERED_CONTENT_YES"], tp-yt-paper-radio-button[name="ALTERED_CONTENT_YES"]'
                r = p.locator(sel)
                if not (r.count() and r.first.is_visible()):
                    more = p.locator("#toggle-button").filter(has_text=re.compile(r"(Show more|더보기)"))
                    if more.count():
                        more.first.click()
                        p.wait_for_timeout(500)
                r = p.locator(sel)
                if r.count():
                    r.first.wait_for(state="visible", timeout=self.t)
                    r.first.click()
                    return
                sec = p.locator("ytcp-video-metadata-altered-content, #altered-content")
                if sec.count():
                    sec.first.get_by_text(TXT_YES).first.click()
                else:
                    self.log("[yt-web] altered-content control not found; leaving default (disclose manually if Studio asks)")
            self._step("altered_content", altered)

        def nxt(n: int):
            def go():
                b = p.locator("#next-button").first
                b.wait_for(state="visible", timeout=self.t)
                b.click()
                p.wait_for_timeout(700)
            return go
        for i in (1, 2, 3):
            self._step(f"next{i}", nxt(i))

        def visibility():
            name = {"public": "PUBLIC", "unlisted": "UNLISTED", "private": "PRIVATE"}[privacy]
            r = p.locator(f'tp-yt-paper-radio-button[name="{name}"]').first
            r.wait_for(state="visible", timeout=self.t)
            r.click()
        self._step("visibility", visibility)

        def wait_upload_done():
            # Done stays disabled until the upload (and checks) finish. Poll aria-disabled, up to 15 min.
            b = p.locator("#done-button").first
            b.wait_for(state="visible", timeout=self.t)
            t0 = time.time()
            while time.time() - t0 < 900:
                dis = b.get_attribute("aria-disabled") or b.get_attribute("disabled")
                if dis in (None, "false", ""):
                    return
                time.sleep(3)
            raise TimeoutError("done button still disabled after 15 min")
        self._step("wait_upload", wait_upload_done)

        video_id = ""

        def done():
            nonlocal video_id
            link = p.locator("a.ytcp-video-info, ytcp-video-info a, a[href*='youtu.be/'], a[href*='youtube.com/watch']").first
            try:
                link.wait_for(state="visible", timeout=10_000)
                href = link.get_attribute("href") or ""
                m = re.search(r"(?:youtu\.be/|v=|shorts/)([A-Za-z0-9_-]{11})", href)
                if m:
                    video_id = m.group(1)
            except Exception:
                pass
            p.locator("#done-button").first.click()
            p.wait_for_timeout(1500)
            close = p.locator("#close-button, ytcp-button#close-button").first
            if close.count():
                try:
                    close.click(timeout=5_000)
                except Exception:
                    pass
        self._step("done", done)
        if not video_id:
            self.log("[yt-web] published, but could not read the video id from the dialog")
            video_id = f"web-{int(time.time())}"
        self.log(f"[yt-web] published {video_id}")
        return video_id
