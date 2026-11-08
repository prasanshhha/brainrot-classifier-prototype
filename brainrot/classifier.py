"""Orchestrates local analysis + Gemini into a single brain rot verdict."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .schema import ClassificationResult, GeminiVerdict, LocalSignals
from .signals import analyze_video, extract_audio

logger = logging.getLogger(__name__)

YOUTUBE_RE = re.compile(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", re.I)
MODES = ("native", "frames")


class Backend(Protocol):
    model: str

    def video_file_part(self, path: Path, fps: float | None = None) -> tuple[Any, str]: ...
    def youtube_part(self, url: str, fps: float | None = None) -> Any: ...
    def bytes_part(self, data: bytes, mime_type: str) -> Any: ...
    def text_part(self, text: str) -> Any: ...
    def delete_file(self, name: str) -> None: ...
    def classify(self, parts: list[Any], prompt: str) -> GeminiVerdict: ...


NO_SIGNALS_URL = "  (not available - video was analysed by URL without downloading)"
NO_SIGNALS_LIVE = "  (not available - frames were captured live in the browser, so cut rate and audio were not measured)"


def _format_signals(s: LocalSignals | None, missing: str = NO_SIGNALS_URL) -> str:
    if s is None:
        return missing
    vol = f"{s.mean_volume_db:.1f} dB" if s.mean_volume_db is not None else "n/a"
    return (
        f"  - duration: {s.duration_s:.1f}s, resolution {s.width}x{s.height} "
        f"({'vertical/short-form' if s.is_vertical else 'horizontal'})\n"
        f"  - scene cuts: {s.scene_cuts} ({s.cuts_per_minute:.1f} cuts/minute)\n"
        f"  - mean in-shot motion: {s.mean_motion:.1f} (0-255 scale; >15 is very busy)\n"
        f"  - audio track: {'yes' if s.has_audio else 'no'}, mean volume {vol} (> -15 dB is loud)"
    )


class BrainRotClassifier:
    def __init__(self, settings: Settings | None = None, backend: Backend | None = None):
        self.settings = settings or Settings()
        if self.settings.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.settings.mode!r}")
        if backend is None:
            from .gemini_client import GeminiBackend
            backend = GeminiBackend(self.settings.api_key, self.settings.model, self.settings.temperature)
        self.backend = backend
        self.prompt_template = resources.files("brainrot").joinpath("prompts/classify.txt").read_text()

    # ---------- public API ----------
    def classify(self, source: str | Path, context: str = "", download: bool = False) -> ClassificationResult:
        """Classify a local video file or a URL.

        Public YouTube URLs are passed straight to Gemini in native mode (no local pacing signals).
        Other URLs - or YouTube with download=True / frames mode - are fetched with yt-dlp first.
        `context` is optional extra info, e.g. the post caption or hashtags."""
        src = str(source)
        if re.match(r"^https?://", src):
            if YOUTUBE_RE.match(src) and self.settings.mode == "native" and not download:
                return self._classify_youtube(src, context)
            with tempfile.TemporaryDirectory() as tmp:
                path = _download(src, Path(tmp))
                return self._classify_file(path, context, source_label=src)
        path = Path(src)
        if not path.is_file():
            raise FileNotFoundError(f"No such video file: {path}")
        return self._classify_file(path, context, source_label=str(path))

    # ---------- internals ----------
    def classify_frames(self, frames: list[bytes], source: str = "", context: str = "",
                        interval_s: float = 0.5) -> ClassificationResult:
        """Classify JPEG screenshots taken by the browser extension (no audio, no local signals)."""
        if not frames:
            raise ValueError("no frames to classify")
        t0 = time.time()
        parts: list[Any] = []
        for i, jpeg in enumerate(frames):
            parts.append(self.backend.text_part(f"Frame {i + 1} at 00:{i * interval_s:04.1f}"))
            parts.append(self.backend.bytes_part(jpeg, "image/jpeg"))
        note = (f"\nNote: you are seeing {len(frames)} screenshots taken {interval_s:g}s apart from the first "
                f"seconds of a short video playing in a browser, with no audio. Judge from the visuals, "
                f"on-screen text and pace you can see, and lower your confidence to match.\n")
        prompt = self._build_prompt(None, context, extra=note, missing=NO_SIGNALS_LIVE)
        result = self._finish(self.backend.classify(parts, prompt), source, None, t0)
        result.mode = "browser_frames"
        return result

    def _build_prompt(self, signals: LocalSignals | None, context: str, extra: str = "",
                      missing: str = NO_SIGNALS_URL) -> str:
        user_ctx = f"\n## Extra context supplied by the user\n{context.strip()}\n" if context.strip() else ""
        prompt = self.prompt_template.replace("{SIGNALS}", _format_signals(signals, missing))
        return prompt.replace("{USER_CONTEXT}", user_ctx + extra)

    def _finish(self, verdict: GeminiVerdict, source: str, signals: LocalSignals | None, t0: float) -> ClassificationResult:
        is_rot = verdict.brain_rot_score >= self.settings.threshold
        return ClassificationResult(
            source=source,
            label="brain_rot" if is_rot else "not_brain_rot",
            is_brain_rot=is_rot,
            brain_rot_score=verdict.brain_rot_score,
            threshold=self.settings.threshold,
            confidence=verdict.confidence,
            mode=self.settings.mode,
            model=self.backend.model,
            verdict=verdict,
            signals=signals,
            elapsed_s=round(time.time() - t0, 2),
        )

    def _classify_youtube(self, url: str, context: str) -> ClassificationResult:
        t0 = time.time()
        part = self.backend.youtube_part(url, fps=self.settings.native_fps)
        verdict = self.backend.classify([part], self._build_prompt(None, context))
        return self._finish(verdict, url, None, t0)

    def _classify_file(self, path: Path, context: str, source_label: str) -> ClassificationResult:
        t0 = time.time()
        s = self.settings
        want_frames = s.mode == "frames"
        logger.info("Analysing %s locally", path.name)
        signals, keyframes = analyze_video(path, max_seconds=s.max_seconds, max_frames=s.max_frames,
                                           extract_keyframes=want_frames)

        if s.mode == "native":
            clip = path
            with tempfile.TemporaryDirectory() as tmp:
                if s.max_seconds and signals.duration_s >= s.max_seconds - 0.5:
                    clip = _trim(path, Path(tmp) / f"clip{path.suffix or '.mp4'}", s.max_seconds) or path
                part, file_name = self.backend.video_file_part(clip, fps=s.native_fps)
            try:
                verdict = self.backend.classify([part], self._build_prompt(signals, context))
            finally:
                self.backend.delete_file(file_name)
            return self._finish(verdict, source_label, signals, t0)

        # frames mode: keyframes + compressed audio, all inline (no upload)
        parts: list[Any] = []
        for kf in keyframes:
            parts.append(self.backend.text_part(f"Keyframe {kf.index} at {int(kf.timestamp // 60):02d}:{kf.timestamp % 60:04.1f}"))
            parts.append(self.backend.bytes_part(kf.jpeg, "image/jpeg"))
        note = (f"\nNote: you are seeing {len(keyframes)} keyframes sampled across the video"
                f"{' plus its audio track' if signals.has_audio else ''}, not the full video.\n")
        if signals.has_audio:
            with tempfile.TemporaryDirectory() as tmp:
                audio = extract_audio(path, Path(tmp) / "audio.mp3", s.max_seconds)
                if audio:
                    parts.append(self.backend.text_part("Audio track:"))
                    parts.append(self.backend.bytes_part(audio.read_bytes(), "audio/mp3"))
        verdict = self.backend.classify(parts, self._build_prompt(signals, context, extra=note))
        return self._finish(verdict, source_label, signals, t0)


def _trim(path: Path, out: Path, seconds: float) -> Path | None:
    """Cut the first N seconds without re-encoding (fast). Falls back to full video on failure."""
    if not shutil.which("ffmpeg"):
        return None
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
                        "-t", str(seconds), "-c", "copy", str(out)], capture_output=True, timeout=300)
    return out if r.returncode == 0 and out.exists() and out.stat().st_size > 0 else None


def _download(url: str, dest: Path) -> Path:
    """Download TikTok/Instagram/YouTube/etc with yt-dlp (must be installed)."""
    if not shutil.which("yt-dlp"):
        raise RuntimeError("yt-dlp is required to analyse non-YouTube URLs: pip install yt-dlp")
    logger.info("Downloading %s with yt-dlp", url)
    r = subprocess.run(
        ["yt-dlp", "-f", "best[height<=720][ext=mp4]/best[height<=720]/best", "--no-playlist",
         "-o", str(dest / "video.%(ext)s"), url],
        capture_output=True, text=True, timeout=600,
    )
    files = [p for p in dest.iterdir() if p.name.startswith("video.")]
    if r.returncode != 0 or not files:
        raise RuntimeError(f"yt-dlp failed: {r.stderr.strip()[-500:]}")
    return files[0]
