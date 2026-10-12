"""Orchestrate ingest → script → TTS with local-first provider selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .scrape import ScrapeResult, fetch_blog, from_pasted_text
from .script import ScriptResult, generate_script
from .tts import AudioResult, synthesize_speech, write_podcast_file


@dataclass
class PipelineResult:
    article: ScrapeResult
    script: ScriptResult
    audio: AudioResult
    badges: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    podcast_path: Optional[str] = None


def run_pipeline(
    *,
    mode: Literal["url", "paste"] = "paste",
    url: Optional[str] = None,
    text: Optional[str] = None,
    title: Optional[str] = None,
    firecrawl_key: Optional[str] = None,
    openai_key: Optional[str] = None,
    openai_model: Optional[str] = None,
    elevenlabs_key: Optional[str] = None,
    elevenlabs_voice_id: Optional[str] = None,
    ollama_host: Optional[str] = None,
    ollama_model: Optional[str] = None,
    podcast_host_name: Optional[str] = None,
    prefer_mock: bool = False,
    save_file: bool = True,
) -> PipelineResult:
    warnings: list[str] = []

    if mode == "paste":
        if not (text or "").strip():
            raise ValueError("Paste some article text first.")
        article = from_pasted_text(text or "", title=title or "Pasted article")
    elif mode == "url":
        if not (url or "").strip():
            raise ValueError("Enter a blog URL.")
        article, w = fetch_blog(
            url or "",
            firecrawl_key=firecrawl_key or None,
            prefer_mock=prefer_mock,
        )
        warnings.extend(w)
    else:
        raise ValueError("mode must be 'url' or 'paste'")

    script, w = generate_script(
        article.title,
        article.text,
        openai_key=openai_key or None,
        openai_model=openai_model,
        ollama_host=ollama_host,
        ollama_model=ollama_model,
        podcast_host_name=podcast_host_name,
    )
    warnings.extend(w)

    audio, w = synthesize_speech(
        parts=script.parts,
        elevenlabs_key=elevenlabs_key or None,
        elevenlabs_voice_id=elevenlabs_voice_id,
    )
    warnings.extend(w)

    podcast_path = None
    if save_file:
        path = write_podcast_file(audio.mp3_bytes, "podcast.mp3")
        podcast_path = str(path)

    badges = {
        "ingest": article.source,
        "script": script.provider,
        "audio": audio.provider,
        "music": getattr(audio, "music_mode", None)
        or ("cues" if audio.music_mixed else "none"),
    }
    if script.model:
        badges["script_model"] = script.model

    return PipelineResult(
        article=article,
        script=script,
        audio=audio,
        badges=badges,
        warnings=warnings,
        podcast_path=podcast_path,
    )
