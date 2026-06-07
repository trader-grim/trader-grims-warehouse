"""PP-WHISPER-001 — tests for `tgw whispertosuggest`.

The subprocess calls (ffmpeg, whisper-cli) are mocked, so this verifies the
plumbing — model-missing guard, transcript cleaning, and cmd_suggest dispatch —
without needing the ggml model (absent on disk) or real audio.
"""

import subprocess

import pytest

import tgw.api as api


@pytest.fixture
def env(tmp_path, monkeypatch):
    wav = tmp_path / "note.wav"
    wav.write_bytes(b"RIFFfake")
    model = tmp_path / "ggml-base.en.bin"
    model.write_bytes(b"fakemodel")
    cfg = {"whisper_bin": "/usr/local/bin/whisper-cli",
           "whisper_model": str(model),
           "plan_vault_path": tmp_path}

    suggested = {}
    monkeypatch.setattr(api, "cmd_suggest",
                        lambda cfg, text: suggested.update(text=text) or {"ok": True, "written": text})

    def fake_run(cmd, *a, **k):
        if cmd[0] == "ffmpeg":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        # whisper-cli -nt
        return subprocess.CompletedProcess(
            cmd, 0, stdout="  Blue ceramic vase  \n  maker mark on base \n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return {"cfg": cfg, "wav": wav, "model": model, "suggested": suggested}


def test_clean_transcript_collapses_whitespace():
    assert api._clean_transcript("  hello \n\n  world  \n") == "hello world"


def test_happy_path_files_suggestion(env):
    out = api.cmd_whisper_to_suggest(env["cfg"], str(env["wav"]))
    assert out["ok"] is True
    assert out["transcript"] == "Blue ceramic vase maker mark on base"
    assert env["suggested"]["text"] == "Blue ceramic vase maker mark on base"


def test_audio_file_missing(env):
    out = api.cmd_whisper_to_suggest(env["cfg"], "/nope/missing.wav")
    assert out["ok"] is False
    assert "audio file not found" in out["error"]


def test_model_missing_is_graceful(env, tmp_path):
    cfg = dict(env["cfg"], whisper_model=str(tmp_path / "absent.bin"))
    out = api.cmd_whisper_to_suggest(cfg, str(env["wav"]))
    assert out["ok"] is False
    assert "whisper model not found" in out["error"]


def test_whisper_failure_is_caught(env, monkeypatch):
    def boom(cmd, *a, **k):
        if cmd[0] == "ffmpeg":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise subprocess.CalledProcessError(1, cmd, stderr="model load error")

    monkeypatch.setattr(subprocess, "run", boom)
    out = api.cmd_whisper_to_suggest(env["cfg"], str(env["wav"]))
    assert out["ok"] is False
    assert "whisper-cli failed" in out["error"]


def test_empty_transcript_rejected(env, monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, stdout="   \n", stderr=""))
    out = api.cmd_whisper_to_suggest(env["cfg"], str(env["wav"]))
    assert out["ok"] is False
    assert "empty transcript" in out["error"]
