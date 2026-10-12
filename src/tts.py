"""TTS + fixed episode assembly: INTRO_MUSIC → host → headlines → [TOPIC → dive]* → ending → OUTRO."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .script import EpisodePart, prepare_spoken

_REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIO_OUTPUT_DIR = _REPO_ROOT / "audio_output"
SAMPLES_DIR = _REPO_ROOT / "samples"

DEFAULT_ELEVEN_VOICE = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_EDGE_VOICE = "en-US-AriaNeural"
DEFAULT_EDGE_RATE = "-8%"
DEFAULT_EDGE_PITCH = "+2Hz"
GAP_MS = 160


@dataclass
class AudioResult:
    mp3_bytes: bytes
    provider: str  # edge-tts | elevenlabs | stub
    mime: str = "audio/mpeg"
    music_mixed: bool = False
    music_mode: str = "none"  # none | cues
    assembly: list[str] | None = None  # ordered kinds for smoke tests


def write_podcast_file(mp3_bytes: bytes, filename: str = "podcast.mp3") -> Path:
    AUDIO_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_OUTPUT_DIR / filename
    path.write_bytes(mp3_bytes)
    return path


def _minimal_silent_mp3() -> bytes:
    frame_header = bytes.fromhex("fff340c4")
    frame = frame_header + (b"\x00" * 69)
    return frame * 40


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError(
            "ffmpeg not found on PATH — required to place musical cues. "
            "Install it (macOS: `brew install ffmpeg`) and restart the app."
        )
    return path


def resolve_sample(name: str) -> Path:
    env_map = {
        "podcast_sting_intro.mp3": "PODCAST_STING_INTRO",
        "podcast_sting_mid.mp3": "PODCAST_STING_MID",
        "podcast_sting_outro.mp3": "PODCAST_STING_OUTRO",
    }
    env_key = env_map.get(name, "")
    if env_key and os.getenv(env_key):
        p = Path(os.environ[env_key]).expanduser()
        if p.is_file():
            return p.resolve()
    for base in (SAMPLES_DIR, _REPO_ROOT / "samples", Path.cwd() / "samples"):
        candidate = base / name
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Sample not found: {name} (expected under {SAMPLES_DIR})")


async def _edge_tts_bytes(
    text: str,
    voice: str = DEFAULT_EDGE_VOICE,
    rate: str = DEFAULT_EDGE_RATE,
    pitch: str = DEFAULT_EDGE_PITCH,
) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    buf = __import__("io").BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    data = buf.getvalue()
    if not data:
        raise RuntimeError("edge-tts returned empty audio")
    return data


def _synthesize_edge_tts(
    text: str,
    voice: Optional[str] = None,
    rate: Optional[str] = None,
    pitch: Optional[str] = None,
) -> bytes:
    voice = voice or os.getenv("EDGE_TTS_VOICE", DEFAULT_EDGE_VOICE)
    rate = rate or os.getenv("EDGE_TTS_RATE", DEFAULT_EDGE_RATE)
    pitch = pitch or os.getenv("EDGE_TTS_PITCH", DEFAULT_EDGE_PITCH)
    return asyncio.run(_edge_tts_bytes(text, voice=voice, rate=rate, pitch=pitch))


def _synthesize_elevenlabs(text: str, api_key: str, voice_id: Optional[str] = None) -> bytes:
    from elevenlabs import ElevenLabs

    client = ElevenLabs(api_key=api_key)
    voice = voice_id or os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_ELEVEN_VOICE)
    audio_generator = client.text_to_speech.convert(
        text=text,
        voice_id=voice,
        model_id="eleven_multilingual_v2",
    )
    chunks: list[bytes] = []
    for chunk in audio_generator:
        if chunk:
            chunks.append(chunk if isinstance(chunk, (bytes, bytearray)) else bytes(chunk))
    mp3 = b"".join(chunks)
    if not mp3:
        raise RuntimeError("ElevenLabs returned empty audio")
    return mp3


def stub_audio_bytes() -> bytes:
    stub_path = SAMPLES_DIR / "stub.mp3"
    if stub_path.exists():
        return stub_path.read_bytes()
    return _minimal_silent_mp3()


def _ffmpeg_concat_mp3s(parts: list[Path], out_file: Path) -> None:
    ffmpeg = require_ffmpeg()
    list_file = out_file.parent / "concat.txt"
    lines = []
    for p in parts:
        escaped = str(p.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(out_file),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_file.exists():
        raise RuntimeError(
            f"ffmpeg concat failed (exit {proc.returncode}): {(proc.stderr or '')[-500:]}"
        )


def _make_silence_mp3(path: Path, ms: int = GAP_MS) -> Path:
    ffmpeg = require_ffmpeg()
    dur = max(0.05, ms / 1000.0)
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=stereo",
        "-t",
        f"{dur:.3f}",
        "-q:a",
        "9",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg silence failed: {(proc.stderr or '')[-300:]}")
    return path


def _tts_one(
    text: str,
    *,
    elevenlabs_key: Optional[str],
    elevenlabs_voice_id: Optional[str],
    edge_voice: Optional[str],
    edge_rate: Optional[str],
    edge_pitch: Optional[str],
    force_stub: bool = False,
) -> tuple[bytes, str, Optional[str]]:
    """Returns (mp3, provider, warning)."""
    text = prepare_spoken(text)
    if not text:
        raise ValueError("Empty speech segment")
    if force_stub:
        return stub_audio_bytes(), "stub", None
    if elevenlabs_key:
        try:
            return (
                _synthesize_elevenlabs(text, elevenlabs_key, elevenlabs_voice_id),
                "elevenlabs",
                None,
            )
        except Exception as exc:  # noqa: BLE001
            warn = f"ElevenLabs failed ({exc}); trying edge-tts."
            try:
                return (
                    _synthesize_edge_tts(
                        text, voice=edge_voice, rate=edge_rate, pitch=edge_pitch
                    ),
                    "edge-tts",
                    warn,
                )
            except Exception as exc2:  # noqa: BLE001
                return (
                    stub_audio_bytes(),
                    "stub",
                    f"{warn} edge-tts failed ({exc2}); using stub.",
                )
    try:
        return (
            _synthesize_edge_tts(text, voice=edge_voice, rate=edge_rate, pitch=edge_pitch),
            "edge-tts",
            None,
        )
    except Exception as exc:  # noqa: BLE001
        return (
            stub_audio_bytes(),
            "stub",
            f"edge-tts failed ({exc}); using stub demo audio.",
        )


def assemble_episode(
    parts: list[EpisodePart],
    *,
    elevenlabs_key: Optional[str] = None,
    elevenlabs_voice_id: Optional[str] = None,
    edge_voice: Optional[str] = None,
    edge_rate: Optional[str] = None,
    edge_pitch: Optional[str] = None,
) -> tuple[bytes, str, list[str], list[str]]:
    """
    Fixed order assembly.
    Returns (mp3_bytes, provider, warnings, assembly_kinds).
    """
    require_ffmpeg()
    warnings: list[str] = []
    assembly: list[str] = []
    provider = "edge-tts"
    force_stub = False

    sting_map = {
        "cue_intro": "podcast_sting_intro.mp3",
        "cue_topic": "podcast_sting_mid.mp3",
        "cue_outro": "podcast_sting_outro.mp3",
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        silence = _make_silence_mp3(tmp_path / "gap.mp3", GAP_MS)
        chain: list[Path] = []
        idx = 0

        for part in parts:
            assembly.append(part.kind)
            if part.kind in sting_map:
                chain.append(resolve_sample(sting_map[part.kind]))
                chain.append(silence)
                continue

            if not part.text.strip():
                continue
            blob, prov, warn = _tts_one(
                part.text,
                elevenlabs_key=elevenlabs_key,
                elevenlabs_voice_id=elevenlabs_voice_id,
                edge_voice=edge_voice,
                edge_rate=edge_rate,
                edge_pitch=edge_pitch,
                force_stub=force_stub,
            )
            if warn:
                warnings.append(warn)
            provider = prov
            if prov == "stub":
                force_stub = True
            speech_path = tmp_path / f"speech_{idx}.mp3"
            speech_path.write_bytes(blob)
            chain.append(speech_path)
            chain.append(silence)
            idx += 1

        if not chain:
            raise RuntimeError("Episode assembly produced no audio clips")

        out_file = tmp_path / "episode.mp3"
        _ffmpeg_concat_mp3s(chain, out_file)
        data = out_file.read_bytes()
        if len(data) < 1000:
            raise RuntimeError("Episode mix produced empty output")
        return data, provider, warnings, assembly


def synthesize_speech(
    script: str = "",
    *,
    parts: Optional[list[EpisodePart]] = None,
    elevenlabs_key: Optional[str] = None,
    elevenlabs_voice_id: Optional[str] = None,
    edge_voice: Optional[str] = None,
    edge_rate: Optional[str] = None,
    edge_pitch: Optional[str] = None,
    mix_music: bool = True,
) -> tuple[AudioResult, list[str]]:
    """Assemble fixed-format episode. `parts` preferred; falls back to parsing markers in script."""
    from .script import parse_marked_episode

    warnings: list[str] = []
    episode_parts = parts
    if not episode_parts:
        if script and "<<<" in script:
            episode_parts = parse_marked_episode(script)
        else:
            raise ValueError("Episode parts missing — regenerate the script.")

    if not mix_music:
        # Speech-only: skip cue kinds
        speech_only = [p for p in episode_parts if p.text.strip()]
        if not speech_only:
            raise ValueError("No spoken segments")
        blobs = []
        provider = "edge-tts"
        force_stub = False
        for p in speech_only:
            blob, prov, warn = _tts_one(
                p.text,
                elevenlabs_key=elevenlabs_key,
                elevenlabs_voice_id=elevenlabs_voice_id,
                edge_voice=edge_voice,
                edge_rate=edge_rate,
                edge_pitch=edge_pitch,
                force_stub=force_stub,
            )
            if warn:
                warnings.append(warn)
            provider = prov
            if prov == "stub":
                force_stub = True
            blobs.append(blob)
        if len(blobs) == 1:
            return (
                AudioResult(
                    mp3_bytes=blobs[0],
                    provider=provider,
                    music_mode="none",
                    assembly=[p.kind for p in speech_only],
                ),
                warnings,
            )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = []
            for i, blob in enumerate(blobs):
                p = tmp_path / f"s{i}.mp3"
                p.write_bytes(blob)
                paths.append(p)
            out = tmp_path / "speech_only.mp3"
            _ffmpeg_concat_mp3s(paths, out)
            return (
                AudioResult(
                    mp3_bytes=out.read_bytes(),
                    provider=provider,
                    music_mode="none",
                    assembly=[p.kind for p in speech_only],
                ),
                warnings,
            )

    try:
        data, provider, tw, assembly = assemble_episode(
            episode_parts,
            elevenlabs_key=elevenlabs_key,
            elevenlabs_voice_id=elevenlabs_voice_id,
            edge_voice=edge_voice,
            edge_rate=edge_rate,
            edge_pitch=edge_pitch,
        )
        warnings.extend(tw)
        return (
            AudioResult(
                mp3_bytes=data,
                provider=provider,
                music_mixed=True,
                music_mode="cues",
                assembly=assembly,
            ),
            warnings,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Musical cues mix failed — speech-only MP3. {exc} "
            "Install ffmpeg (`brew install ffmpeg`) and ensure "
            "samples/podcast_sting_*.mp3 are present."
        )
        fallback, fw = synthesize_speech(
            parts=episode_parts,
            elevenlabs_key=elevenlabs_key,
            elevenlabs_voice_id=elevenlabs_voice_id,
            edge_voice=edge_voice,
            edge_rate=edge_rate,
            edge_pitch=edge_pitch,
            mix_music=False,
        )
        warnings.extend(fw)
        return fallback, warnings
