"""shortforge — unattended faceless Shorts factory.

Pipeline: source item (grounded, licensed) -> script (grounding-gated) -> TTS
(per-sentence, exact caption timing) -> ffmpeg render (1080x1920) -> upload
(YouTube Data API v3) -> ledger.
"""

__version__ = "2.0.0"
