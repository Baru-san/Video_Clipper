from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import app


def _wait(client: TestClient, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish in time")


def _upload(client: TestClient, path, name: str = "sample.mp4"):
    with open(path, "rb") as fh:
        return client.post("/api/uploads", files={"file": (name, fh, "video/mp4")})


def test_health():
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}


def test_upload_probe_and_copy_clip(sample_video):
    with TestClient(app) as client:
        response = _upload(client, sample_video)
        assert response.status_code == 200
        upload = response.json()
        assert upload["metadata"]["duration"] > 3
        assert upload["metadata"]["width"] == 320
        assert upload["thumbnail_urls"]

        thumb = client.get(upload["thumbnail_urls"][0])
        assert thumb.status_code == 200

        keyframes = client.get(f"/api/uploads/{upload['upload_id']}/keyframes")
        assert keyframes.status_code == 200
        assert len(keyframes.json()["keyframes"]) >= 1

        created = client.post(
            "/api/clips",
            json={
                "upload_id": upload["upload_id"],
                "start": 1.0,
                "end": 3.0,
                "mode": "copy",
            },
        )
        assert created.status_code == 200
        job = _wait(client, created.json()["id"])
        assert job["status"] == "done"
        assert job["effective_start"] is not None

        download = client.get(job["download_url"])
        assert download.status_code == 200
        assert len(download.content) > 0


def test_reencode_clip(sample_video):
    with TestClient(app) as client:
        upload = _upload(client, sample_video).json()
        created = client.post(
            "/api/clips",
            json={
                "upload_id": upload["upload_id"],
                "start": 0.5,
                "end": 2.5,
                "mode": "reencode",
                "scale_height": 240,
            },
        )
        job = _wait(client, created.json()["id"])
        assert job["status"] == "done"


def test_reject_reversed_range(sample_video):
    with TestClient(app) as client:
        upload = _upload(client, sample_video).json()
        response = client.post(
            "/api/clips",
            json={
                "upload_id": upload["upload_id"],
                "start": 3.0,
                "end": 1.0,
                "mode": "copy",
            },
        )
        assert response.status_code == 422


def test_reject_non_video(tmp_path):
    bogus = tmp_path / "not_video.mp4"
    bogus.write_bytes(b"this is not a video")
    with TestClient(app) as client:
        response = _upload(client, bogus, name="not_video.mp4")
        assert response.status_code == 415
