"""Instagram Reels upload through instagram.com's desktop web UI (no Meta app, no token).

Instagram's DOM has no stable ids; aria-labels and button text are the handles, so
every locator carries an English + Korean regex. Same failure discipline as yt_web:
screenshot + step name. NOT exercised against live instagram.com from the build
container; exercised against a local look-alike page.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

HOME = "https://www.instagram.com/"
L_NEW_POST = re.compile(r"^(New post|Create|새 게시물|새로운 게시물|만들기)$", re.I)
L_POST_ITEM = re.compile(r"^(Post|게시물)$", re.I)
T_SELECT = re.compile(r"(Select from computer|컴퓨터에서 선택)", re.I)
T_OK = re.compile(r"^(OK|확인)$", re.I)
T_NEXT = re.compile(r"^(Next|다음)$", re.I)
T_SHARE = re.compile(r"^(Share|공유하기|공유)$", re.I)
L_CROP = re.compile(r"(Select crop|자르기 선택)", re.I)
T_916 = re.compile(r"^9:16$")
L_CAPTION = re.compile(r"(Write a caption|문구 입력)", re.I)
T_SHARED = re.compile(r"(has been shared|공유되었습니다|공유했습니다)", re.I)


class UiStep(RuntimeError):
    def __init__(self, step: str, shot: Path | None, cause):
        super().__init__(f"instagram-web step '{step}' failed: {cause}" + (f" (screenshot {shot})" if shot else ""))
        self.step, self.shot = step, shot


class InstagramWeb:
    def __init__(self, page, shots_dir: Path, log=print, timeout_ms: int = 60_000):
        self.page = page
        self.shots = Path(shots_dir)
        self.shots.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.t = timeout_ms

    def _shot(self, name: str) -> Path | None:
        try:
            p = self.shots / f"ig-{int(time.time())}-{name}.png"
            self.page.screenshot(path=str(p))
            return p
        except Exception:
            return None

    def _step(self, name: str, fn):
        try:
            return fn()
        except Exception as e:
            raise UiStep(name, self._shot(name), e) from e

    def logged_in(self) -> bool:
        self.page.goto(HOME, wait_until="domcontentloaded")
        try:
            self.page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        if "/accounts/login" in self.page.url:
            return False
        return self.page.get_by_label(L_NEW_POST).count() > 0

    def _click_text(self, pattern, role: str = "button"):
        p = self.page
        loc = p.get_by_role(role, name=pattern)
        if loc.count():
            loc.first.click(timeout=self.t)
            return
        p.get_by_text(pattern).first.click(timeout=self.t)

    def upload(self, mp4: Path, caption: str) -> str:
        p = self.page
        self._step("open", lambda: p.goto(HOME, wait_until="domcontentloaded"))
        if "/accounts/login" in p.url:
            raise UiStep("login", self._shot("login"), "not signed in — press 'Instagram 로그인' first")

        def new_post():
            # left nav "Create"/"New post": an svg[aria-label] inside a link — get_by_label matches aria-label
            cand = p.get_by_label(L_NEW_POST)
            cand.first.wait_for(state="visible", timeout=self.t)
            cand.first.click(timeout=self.t)
            p.wait_for_timeout(600)
            sub = p.get_by_text(L_POST_ITEM)
            if sub.count():
                try:
                    sub.first.click(timeout=3_000)
                except Exception:
                    pass
        self._step("new_post", new_post)

        def pick_file():
            inp = p.locator("input[type=file]").last
            inp.wait_for(state="attached", timeout=self.t)
            inp.set_input_files(str(mp4))
        self._step("file", pick_file)

        def reels_notice():
            ok = p.get_by_role("button", name=T_OK)
            try:
                ok.first.click(timeout=5_000)
            except Exception:
                pass  # notice not shown
        self._step("reels_notice", reels_notice)

        def crop_916():
            try:
                p.get_by_label(L_CROP).first.click(timeout=8_000)
                p.get_by_text(T_916).first.click(timeout=8_000)
            except Exception:
                self.log("[ig-web] crop selector not found; the video is already 9:16, continuing")
        self._step("crop", crop_916)

        self._step("next1", lambda: self._click_text(T_NEXT))
        self._step("next2", lambda: self._click_text(T_NEXT))

        def write_caption():
            box = p.get_by_label(L_CAPTION)
            if not box.count():
                box = p.locator("div[contenteditable='true'][role='textbox']")
            box.first.click(timeout=self.t)
            p.keyboard.type(caption[:2200], delay=1)
        self._step("caption", write_caption)

        self._step("share", lambda: self._click_text(T_SHARE))

        def wait_shared():
            p.get_by_text(T_SHARED).first.wait_for(state="visible", timeout=600_000)
        self._step("wait_shared", wait_shared)
        rid = f"web-{int(time.time())}"
        self.log(f"[ig-web] shared ({rid})")
        return rid
