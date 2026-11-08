"""Minimal web UI: upload a video or paste a URL, get a verdict."""

from __future__ import annotations

import logging
import shutil
import tempfile
from importlib import resources
from pathlib import Path

import base64
import binascii
from collections import OrderedDict

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..classifier import BrainRotClassifier
from ..config import Settings

logger = logging.getLogger(__name__)
app = FastAPI(title="Brain Rot Detector")
MAX_UPLOAD_MB = 500
MAX_FRAMES = 16
CACHE_SIZE = 2000
_cache: OrderedDict[tuple[str, str], dict] = OrderedDict()


class PredictRequest(BaseModel):
    """What the browser extension sends for each video."""

    video_id: str
    platform: str = "instagram"
    frames_b64: list[str] = Field(default_factory=list)
    cuts_per_sec: float | None = None


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return resources.files("brainrot.web").joinpath("templates/index.html").read_text()


@app.get("/api/config")
def config() -> dict:
    s = Settings()
    return {"model": s.model, "mode": s.mode, "threshold": s.threshold, "has_api_key": bool(s.api_key)}


@app.get("/health")
def health() -> dict:
    s = Settings()
    return {"ok": True, "model": s.model, "threshold": s.threshold, "has_api_key": bool(s.api_key)}


@app.post("/predict")
async def predict(req: PredictRequest) -> dict:
    """Score one video from the extension's screenshots. Same video asked twice = cached answer."""
    if not req.frames_b64:
        raise HTTPException(400, "frames_b64 is empty")
    key = (req.platform, req.video_id)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    try:
        frames = [base64.b64decode(f.split(",")[-1], validate=True) for f in req.frames_b64[:MAX_FRAMES]]
    except (binascii.Error, ValueError):
        raise HTTPException(400, "frames_b64 must be base64 JPEGs")
    try:
        clf = BrainRotClassifier(Settings())
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))
    try:
        r = await run_in_threadpool(clf.classify_frames, frames, f"{req.platform}:{req.video_id}")
    except Exception as e:
        logger.exception("Frame classification failed")
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    out = {
        "video_id": req.video_id,
        "platform": req.platform,
        "prob": round(r.brain_rot_score / 100, 2),
        "flagged": r.is_brain_rot,
        "label": r.label,
        "score": r.brain_rot_score,
        "threshold": r.threshold,
        "confidence": r.confidence,
        "reasons": r.verdict.detected_tropes[:3],
        "summary": r.verdict.reasoning,
        "model": r.model,
        "elapsed_s": r.elapsed_s,
    }
    _cache[key] = out
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return out


@app.post("/api/classify")
async def classify(
    file: UploadFile | None = File(None),
    url: str = Form(""),
    mode: str = Form("native"),
    threshold: int = Form(50),
    context: str = Form(""),
):
    if not file and not url.strip():
        raise HTTPException(400, "Upload a video or provide a URL.")
    settings = Settings()
    settings.mode = mode
    settings.threshold = threshold
    try:
        clf = BrainRotClassifier(settings)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(400, str(e))

    tmpdir = Path(tempfile.mkdtemp(prefix="brainrot_"))
    try:
        if file and file.filename:
            dest = tmpdir / Path(file.filename).name
            with dest.open("wb") as out:
                size = 0
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > MAX_UPLOAD_MB * 1_000_000:
                        raise HTTPException(413, f"File larger than {MAX_UPLOAD_MB} MB")
                    out.write(chunk)
            result = await run_in_threadpool(clf.classify, dest, context)
            result.source = file.filename
        else:
            result = await run_in_threadpool(clf.classify, url.strip(), context)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Classification failed")
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
