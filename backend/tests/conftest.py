from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

os.environ.setdefault("VC_DATA_DIR", tempfile.mkdtemp(prefix="vc_test_"))
os.environ.setdefault("VC_MIN_FREE_BYTES", "0")

import pytest


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    output = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=4:size=320x240:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output
