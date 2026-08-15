# FastAPI entrypoint

from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from tools.higgsfield import HiggsfieldClient, HiggsfieldError

app = FastAPI(title="OpenClaw")


class Text2ImageRequest(BaseModel):
    prompt: str
    width_and_height: str = "1536x2048"
    quality: str = "1080p"
    batch_size: int = 1
    style_id: Optional[str] = None
    seed: Optional[int] = None


class Image2VideoRequest(BaseModel):
    image_url: str
    prompt: str = ""
    model: str = "dop-standard"
    motion_ids: Optional[list[str]] = None
    seed: Optional[int] = None


def _client() -> HiggsfieldClient:
    try:
        return HiggsfieldClient()
    except HiggsfieldError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/higgsfield/text2image")
def higgsfield_text2image(req: Text2ImageRequest) -> dict:
    """Submit a Higgsfield Soul text-to-image job; returns the job set."""
    with _client() as client:
        try:
            return client.text2image(**req.model_dump())
        except HiggsfieldError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/higgsfield/image2video")
def higgsfield_image2video(req: Image2VideoRequest) -> dict:
    """Submit a Higgsfield DoP image-to-video job; returns the job set."""
    with _client() as client:
        try:
            return client.image2video(**req.model_dump())
        except HiggsfieldError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/higgsfield/jobs/{job_set_id}")
def higgsfield_job_status(job_set_id: str) -> dict:
    """Fetch job set status and any result URLs."""
    with _client() as client:
        try:
            job_set = client.get_job_set(job_set_id)
        except HiggsfieldError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"job_set": job_set, "result_urls": HiggsfieldClient.result_urls(job_set)}
