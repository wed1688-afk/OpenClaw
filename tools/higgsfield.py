"""Higgsfield AI integration.

Wraps the official Higgsfield platform API (https://platform.higgsfield.ai)
for text-to-image (Soul) and image-to-video (DoP) generation.

Credentials come from the Higgsfield Cloud dashboard and are read from the
``HF_API_KEY`` and ``HF_SECRET`` environment variables, or passed explicitly
to :class:`HiggsfieldClient`.

Usage::

    from tools.higgsfield import HiggsfieldClient

    client = HiggsfieldClient()
    job_set = client.text2image(prompt="a cat astronaut, cinematic")
    job_set = client.wait_for_completion(job_set["id"])
    urls = client.result_urls(job_set)
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx

DEFAULT_BASE_URL = "https://platform.higgsfield.ai"

# Job statuses reported by the platform API.
TERMINAL_STATUSES = {"completed", "failed", "nsfw", "canceled"}


class HiggsfieldError(RuntimeError):
    """Raised on API errors or failed generation jobs."""


class HiggsfieldClient:
    """Minimal synchronous client for the Higgsfield platform API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.getenv("HF_API_KEY")
        self.api_secret = api_secret or os.getenv("HF_SECRET")
        if not self.api_key or not self.api_secret:
            raise HiggsfieldError(
                "Missing Higgsfield credentials: set HF_API_KEY and HF_SECRET "
                "environment variables (from https://cloud.higgsfield.ai) or "
                "pass api_key/api_secret explicitly."
            )
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "hf-api-key": self.api_key,
                "hf-secret": self.api_secret,
                "Content-Type": "application/json",
            },
        )

    # ------------------------------------------------------------------ #
    # Generation endpoints
    # ------------------------------------------------------------------ #

    def text2image(
        self,
        prompt: str,
        width_and_height: str = "1536x2048",
        quality: str = "1080p",
        batch_size: int = 1,
        style_id: Optional[str] = None,
        enhance_prompt: bool = True,
        seed: Optional[int] = None,
        webhook_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ) -> dict[str, Any]:
        """Submit a Soul text-to-image job. Returns the created job set."""
        params: dict[str, Any] = {
            "prompt": prompt,
            "width_and_height": width_and_height,
            "quality": quality,
            "batch_size": batch_size,
            "enhance_prompt": enhance_prompt,
        }
        if style_id:
            params["style_id"] = style_id
        if seed is not None:
            params["seed"] = seed
        return self._submit(
            "/v1/text2image/soul", params, webhook_url, webhook_secret
        )

    def image2video(
        self,
        image_url: str,
        prompt: str = "",
        model: str = "dop-standard",
        motion_ids: Optional[list[str]] = None,
        enhance_prompt: bool = True,
        seed: Optional[int] = None,
        webhook_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ) -> dict[str, Any]:
        """Submit a DoP image-to-video job. Returns the created job set.

        ``model`` is one of ``dop-lite``, ``dop-standard``, ``dop-turbo``.
        ``motion_ids`` are optional Higgsfield motion preset IDs
        (see :meth:`list_motions`).
        """
        params: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "input_images": [{"type": "image_url", "image_url": image_url}],
            "enhance_prompt": enhance_prompt,
        }
        if motion_ids:
            params["motions"] = [{"id": m} for m in motion_ids]
        if seed is not None:
            params["seed"] = seed
        return self._submit(
            "/v1/image2video/dop", params, webhook_url, webhook_secret
        )

    # ------------------------------------------------------------------ #
    # Catalog endpoints
    # ------------------------------------------------------------------ #

    def list_motions(self) -> list[dict[str, Any]]:
        """List available camera/motion presets for image2video."""
        return self._get("/v1/motions")

    def list_soul_styles(self) -> list[dict[str, Any]]:
        """List available Soul style presets for text2image."""
        return self._get("/v1/souls")

    # ------------------------------------------------------------------ #
    # Job polling
    # ------------------------------------------------------------------ #

    def get_job_set(self, job_set_id: str) -> dict[str, Any]:
        """Fetch the current state of a job set."""
        return self._get(f"/v1/job-sets/{job_set_id}")

    def wait_for_completion(
        self,
        job_set_id: str,
        poll_interval: float = 5.0,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Poll until every job in the set reaches a terminal status.

        Raises :class:`HiggsfieldError` if any job fails or the timeout is hit.
        """
        deadline = time.monotonic() + timeout
        while True:
            job_set = self.get_job_set(job_set_id)
            jobs = job_set.get("jobs", [])
            statuses = {job.get("status") for job in jobs}
            if jobs and statuses <= TERMINAL_STATUSES:
                bad = statuses - {"completed"}
                if bad:
                    raise HiggsfieldError(
                        f"Job set {job_set_id} finished with statuses: "
                        f"{sorted(statuses)}"
                    )
                return job_set
            if time.monotonic() >= deadline:
                raise HiggsfieldError(
                    f"Timed out after {timeout}s waiting for job set "
                    f"{job_set_id} (statuses: {sorted(statuses)})"
                )
            time.sleep(poll_interval)

    @staticmethod
    def result_urls(job_set: dict[str, Any]) -> list[str]:
        """Extract result media URLs from a completed job set."""
        urls: list[str] = []
        for job in job_set.get("jobs", []):
            results = job.get("results") or {}
            for variant in ("raw", "min"):
                url = (results.get(variant) or {}).get("url")
                if url:
                    urls.append(url)
                    break
        return urls

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _submit(
        self,
        path: str,
        params: dict[str, Any],
        webhook_url: Optional[str],
        webhook_secret: Optional[str],
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"params": params}
        if webhook_url:
            body["webhook"] = {"url": webhook_url}
            if webhook_secret:
                body["webhook"]["secret"] = webhook_secret
        response = self._http.post(path, json=body)
        return self._parse(response)

    def _get(self, path: str) -> Any:
        return self._parse(self._http.get(path))

    @staticmethod
    def _parse(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise HiggsfieldError(
                f"Higgsfield API error {response.status_code}: {response.text}"
            )
        return response.json()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "HiggsfieldClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
