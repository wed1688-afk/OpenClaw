"""Tests for the Higgsfield integration (all HTTP is mocked)."""

import httpx
import pytest

from tools.higgsfield import HiggsfieldClient, HiggsfieldError


def make_client(handler) -> HiggsfieldClient:
    client = HiggsfieldClient(api_key="test-key", api_secret="test-secret")
    client._http = httpx.Client(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
        headers={"hf-api-key": "test-key", "hf-secret": "test-secret"},
    )
    return client


def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("HF_API_KEY", raising=False)
    monkeypatch.delenv("HF_SECRET", raising=False)
    with pytest.raises(HiggsfieldError, match="HF_API_KEY"):
        HiggsfieldClient()


def test_text2image_submits_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.read()
        captured["headers"] = request.headers
        return httpx.Response(200, json={"id": "js-1", "jobs": []})

    with make_client(handler) as client:
        job_set = client.text2image(prompt="a red fox", batch_size=2)

    assert job_set["id"] == "js-1"
    assert captured["path"] == "/v1/text2image/soul"
    assert captured["headers"]["hf-api-key"] == "test-key"
    assert b'"prompt": "a red fox"' in captured["body"] or b'"prompt":"a red fox"' in captured["body"]


def test_image2video_builds_input_images_and_motions():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.read())
        return httpx.Response(200, json={"id": "js-2", "jobs": []})

    with make_client(handler) as client:
        client.image2video(
            image_url="https://example.com/cat.png",
            prompt="zoom in",
            motion_ids=["m-1"],
        )

    params = captured["body"]["params"]
    assert params["input_images"] == [
        {"type": "image_url", "image_url": "https://example.com/cat.png"}
    ]
    assert params["motions"] == [{"id": "m-1"}]
    assert params["model"] == "dop-standard"


def test_wait_for_completion_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        status = "completed" if calls["n"] >= 2 else "in_progress"
        return httpx.Response(
            200,
            json={
                "id": "js-3",
                "jobs": [
                    {
                        "status": status,
                        "results": {"raw": {"url": "https://cdn/x.mp4"}},
                    }
                ],
            },
        )

    with make_client(handler) as client:
        job_set = client.wait_for_completion("js-3", poll_interval=0)

    assert calls["n"] == 2
    assert HiggsfieldClient.result_urls(job_set) == ["https://cdn/x.mp4"]


def test_wait_for_completion_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "js-4", "jobs": [{"status": "failed"}]}
        )

    with make_client(handler) as client:
        with pytest.raises(HiggsfieldError, match="failed"):
            client.wait_for_completion("js-4", poll_interval=0)


def test_api_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    with make_client(handler) as client:
        with pytest.raises(HiggsfieldError, match="401"):
            client.get_job_set("js-5")
