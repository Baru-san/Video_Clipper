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


def test_resource_listed_and_deleted(sample_video):
    with TestClient(app) as client:
        upload = _upload(client, sample_video).json()
        upload_id = upload["upload_id"]
        created = client.post(
            "/api/clips",
            json={
                "upload_id": upload_id,
                "start": 0.5,
                "end": 2.0,
                "mode": "copy",
            },
        ).json()
        job = _wait(client, created["id"])
        assert job["status"] == "done"

        # Retained for 24h, so it can be downloaded more than once.
        assert client.get(job["download_url"]).status_code == 200
        assert client.get(job["download_url"]).status_code == 200

        items = client.get("/api/resources").json()["items"]
        matches = [item for item in items if item["upload_id"] == upload_id]
        assert len(matches) == 1
        resource_id = matches[0]["id"]
        assert matches[0]["download_url"] == f"/api/resources/{resource_id}/download"

        assert client.get(f"/api/resources/{resource_id}/download").status_code == 200

        assert client.delete(f"/api/resources/{resource_id}").status_code == 204
        assert client.get(f"/api/resources/{resource_id}/download").status_code == 404
        # Removing the last clip for an upload also removes the source.
        assert client.get(f"/api/uploads/{upload_id}").status_code == 404


def test_clip_with_subtitles_and_translation(monkeypatch, sample_video):
    import app.media.stt as stt
    import app.media.translate as translate
    from app.media.subtitles import Segment, Transcript

    monkeypatch.setattr(stt, "is_configured", lambda: True)
    monkeypatch.setattr(translate, "is_configured", lambda: True)

    async def fake_transcribe(path, language=None):
        return Transcript("en", [Segment(0.0, 1.0, "hello"), Segment(1.0, 2.0, "world")])

    async def fake_translate(segments, target, source=None):
        return [Segment(s.start, s.end, f"{target}:{s.text}") for s in segments]

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(translate, "translate_segments", fake_translate)

    with TestClient(app) as client:
        upload = _upload(client, sample_video).json()
        created = client.post(
            "/api/clips",
            json={
                "upload_id": upload["upload_id"],
                "start": 0.0,
                "end": 2.0,
                "mode": "copy",
                "subtitles": "srt",
                "translate_to": "id",
            },
        ).json()
        job = _wait(client, created["id"])
        assert job["status"] == "done"
        assert job["subtitle_status"] == "done"
        assert job["detected_language"] == "en"

        srt = client.get(job["subtitle_url"])
        assert srt.status_code == 200
        assert "id:hello" in srt.text


def test_subtitles_skip_when_language_matches(monkeypatch, sample_video):
    import app.media.stt as stt
    import app.media.translate as translate
    from app.media.subtitles import Segment, Transcript

    monkeypatch.setattr(stt, "is_configured", lambda: True)
    monkeypatch.setattr(translate, "is_configured", lambda: True)

    async def fake_transcribe(path, language=None):
        return Transcript("id", [Segment(0.0, 1.0, "halo dunia")])

    called = {"translate": False}

    async def fake_translate(segments, target, source=None):
        called["translate"] = True
        return segments

    monkeypatch.setattr(stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(translate, "translate_segments", fake_translate)

    with TestClient(app) as client:
        upload = _upload(client, sample_video).json()
        created = client.post(
            "/api/clips",
            json={
                "upload_id": upload["upload_id"],
                "start": 0.0,
                "end": 2.0,
                "mode": "copy",
                "subtitles": "srt",
                "translate_to": "id",
            },
        ).json()
        job = _wait(client, created["id"])
        assert job["subtitle_status"] == "done"
        assert called["translate"] is False
        assert "halo dunia" in client.get(job["subtitle_url"]).text


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
