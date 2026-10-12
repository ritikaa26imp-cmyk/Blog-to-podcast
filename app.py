"""Blog → Podcast — local-first Streamlit UI."""

from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from src.pipeline import run_pipeline

load_dotenv()

st.set_page_config(
    page_title="Blog → Podcast",
    page_icon="🎙️",
    layout="centered",
)

st.title("Blog → Podcast")
st.caption(
    "Works offline/local by default — BeautifulSoup, Ollama (`llama3.2`), and edge-tts. "
    "Add API keys only if you want premium scrape / LLM / voice quality."
)

with st.sidebar:
    st.header("Local settings")
    ollama_host = st.text_input(
        "Ollama host",
        value=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
    )
    ollama_model = st.text_input(
        "Ollama model",
        value=os.getenv("OLLAMA_MODEL", "llama3.2"),
    )
    podcast_host_name = st.text_input(
        "Host name",
        value=os.getenv("PODCAST_HOST_NAME", "Alex"),
    )
    st.divider()
    st.header("Optional paid upgrades")
    st.caption("Leave blank to stay on local defaults.")
    openai_key = st.text_input(
        "OpenAI API Key",
        type="password",
        value=os.getenv("OPENAI_API_KEY", ""),
    )
    firecrawl_key = st.text_input(
        "Firecrawl API Key",
        type="password",
        value=os.getenv("FIRECRAWL_API_KEY", ""),
    )
    elevenlabs_key = st.text_input(
        "ElevenLabs API Key",
        type="password",
        value=os.getenv("ELEVENLABS_API_KEY", ""),
    )
    openai_model = st.text_input(
        "OpenAI model",
        value=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    )
    elevenlabs_voice = st.text_input(
        "ElevenLabs voice id",
        value=os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"),
    )

mode = st.radio("Input", ["Paste text", "Blog URL"], horizontal=True)

url = ""
text = ""
title = "Pasted article"

if mode == "Blog URL":
    url = st.text_input("Blog URL", placeholder="https://example.com/post")
else:
    if "title_input" not in st.session_state:
        st.session_state["title_input"] = "Pasted article"
    if "text_input" not in st.session_state:
        st.session_state["text_input"] = ""

    if st.button("Load sample article", type="secondary"):
        sample_path = os.path.join(os.path.dirname(__file__), "samples", "demo_article.txt")
        if os.path.exists(sample_path):
            raw = open(sample_path, encoding="utf-8").read().strip()
            lines = raw.splitlines()
            st.session_state["title_input"] = lines[0] if lines else "Sample article"
            st.session_state["text_input"] = "\n".join(
                lines[2:] if len(lines) > 2 else lines[1:]
            )
            st.rerun()

    title = st.text_input("Title (optional)", key="title_input")
    text = st.text_area(
        "Article text",
        height=220,
        placeholder="Paste a blog post or notes here…",
        key="text_input",
    )

generate = st.button("Generate Podcast", type="primary", use_container_width=True)

if generate:
    has_input = (mode == "Blog URL" and url.strip()) or (
        mode == "Paste text" and text.strip()
    )
    if not has_input:
        st.warning("Add a blog URL or paste some text first.")
    else:
        progress = st.progress(0, text="Starting…")
        status = st.empty()
        try:
            status.info("Ingesting content…")
            progress.progress(15, text="Ingest")
            # run_pipeline does all stages; we still show staged messaging around it
            status.info("Writing podcast script…")
            progress.progress(45, text="Script")
            status.info("Synthesizing audio…")
            progress.progress(70, text="Audio")

            result = run_pipeline(
                mode="url" if mode == "Blog URL" else "paste",
                url=url,
                text=text,
                title=title,
                firecrawl_key=firecrawl_key.strip() or None,
                openai_key=openai_key.strip() or None,
                openai_model=openai_model.strip() or None,
                elevenlabs_key=elevenlabs_key.strip() or None,
                elevenlabs_voice_id=elevenlabs_voice.strip() or None,
                ollama_host=ollama_host.strip() or None,
                ollama_model=ollama_model.strip() or None,
                podcast_host_name=podcast_host_name.strip() or None,
                save_file=True,
            )

            progress.progress(100, text="Done")
            status.success("Podcast ready.")

            st.session_state["last_result"] = {
                "script": result.script.script,
                "audio": result.audio.mp3_bytes,
                "badges": result.badges,
                "warnings": result.warnings,
                "title": result.article.title,
                "source_preview": result.article.text[:500],
            }
        except Exception as exc:  # noqa: BLE001
            progress.empty()
            status.empty()
            st.error(f"Could not generate podcast: {exc}")

if "last_result" in st.session_state:
    result = st.session_state["last_result"]
    badges = result["badges"]
    cols = st.columns(4)
    cols[0].metric("Ingest", badges.get("ingest", "—"))
    script_label = badges.get("script", "—")
    if badges.get("script_model"):
        script_label = f"{script_label} ({badges['script_model']})"
    cols[1].metric("Script", script_label)
    cols[2].metric("Audio", badges.get("audio", "—"))
    cols[3].metric("Music", badges.get("music", "—"))

    if badges.get("music") == "none":
        st.info(
            "No musical cues mixed. Install ffmpeg (`brew install ffmpeg` on macOS), "
            "confirm `samples/podcast_sting_*.mp3` exist, restart Streamlit, and regenerate."
        )

    for warning in result.get("warnings") or []:
        st.warning(warning)

    st.subheader(result.get("title") or "Episode")
    st.audio(result["audio"], format="audio/mp3")
    st.download_button(
        "Download podcast.mp3",
        data=result["audio"],
        file_name="podcast.mp3",
        mime="audio/mpeg",
        use_container_width=True,
    )
    with st.expander("Podcast script"):
        st.write(result["script"])
    with st.expander("Source preview"):
        st.write(result.get("source_preview") or "")
