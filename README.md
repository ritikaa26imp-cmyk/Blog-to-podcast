# Blog → Podcast

Turn a blog URL or pasted article into a short, structured podcast episode with spoken narration and downloadable MP3 audio.

Runs locally with Streamlit. By default it uses free/local tools (BeautifulSoup, Ollama, edge-tts, ffmpeg). Optional paid APIs (Firecrawl, OpenAI, ElevenLabs) can upgrade quality when you provide keys.

## Features

- **URL or paste** — fetch a public blog post or paste text directly
- **Local-first** — works without paid API keys; Ollama + edge-tts when available
- **Fixed episode format** — intro music, host intro, headlines, topic stings + deep-dives, ending, outro music
- **Provider badges** — see which scrape / script / audio / music path ran
- **Play & download** — in-app player plus `podcast.mp3` download
- **Graceful fallbacks** — heuristic script if Ollama is down; stub audio if TTS fails; speech-only if cue mix fails

## Episode format

Every episode is assembled in this order:

1. Upbeat intro music  
2. Host introduction (default name **Alex**, override with `PODCAST_HOST_NAME`)  
3. Main headlines (2–4 points from the article)  
4. Topic music → deep-dive #1  
5. Topic music → deep-dive #2 (optional #3–#4)  
6. Spoken ending  
7. Upbeat outro music  

There is **no continuous music under speech** — only short cues between sections. Music badge shows `cues` when mix succeeds.

## Requirements

| Tool | Purpose |
|------|---------|
| **Python 3.10+** | App runtime |
| **ffmpeg** | Musical cue mixing (`brew install ffmpeg` on macOS) |
| **Ollama** (optional but recommended) | Local LLM scripts (`ollama pull llama3.2`) |
| **Internet** | BeautifulSoup fetches; edge-tts synthesis |

## Setup

```bash
git clone <your-repo-url>
cd Blog-to-podcast   # or your clone directory

# one-time system deps
brew install ffmpeg                 # macOS
# sudo apt-get install -y ffmpeg    # Debian/Ubuntu

python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# recommended for better scripts
ollama pull llama3.2
```

Copy `.env.example` to `.env` if you want env-based config (optional).

## Run

```bash
streamlit run app.py --server.port 3847 --server.address 127.0.0.1
```

Open [http://127.0.0.1:3847](http://127.0.0.1:3847).

1. Paste article text (or load the sample) / enter a blog URL  
2. Click **Generate Podcast**  
3. Play the episode and download **`podcast.mp3`**

## Environment variables

| Variable | Stage | Notes |
|----------|--------|------|
| `OLLAMA_HOST` | Script | Default `http://127.0.0.1:11434` |
| `OLLAMA_MODEL` | Script | Default `llama3.2` |
| `PODCAST_HOST_NAME` | Script | Default `Alex` |
| `EDGE_TTS_VOICE` | Audio | Default `en-US-AriaNeural` |
| `EDGE_TTS_RATE` | Audio | Default `-8%` |
| `EDGE_TTS_PITCH` | Audio | Default `+2Hz` |
| `FIRECRAWL_API_KEY` | Ingest | Optional paid scrape upgrade |
| `OPENAI_API_KEY` | Script | Optional paid LLM upgrade |
| `OPENAI_MODEL` | Script | Default `gpt-4o-mini` when OpenAI is used |
| `ELEVENLABS_API_KEY` | Audio | Optional paid TTS upgrade |
| `ELEVENLABS_VOICE_ID` | Audio | Optional voice override |
| `PODCAST_STING_INTRO` / `_MID` / `_OUTRO` | Audio | Optional paths to custom cue MP3s |

Leave paid keys blank to stay on local defaults. Paid failures fall back to the local chain.

## Local vs paid paths

| Stage | Default (no keys) | Optional upgrade |
|-------|-------------------|------------------|
| Ingest | BeautifulSoup (or paste); mock sample if scrape fails | Firecrawl |
| Script | Ollama → heuristic template | OpenAI |
| Audio | edge-tts → stub MP3 | ElevenLabs |
| Music | ffmpeg cue mix (`cues`) | — |

## Project structure

```text
.
├── app.py                 # Streamlit UI
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── audio_output/          # generated podcast.mp3 (gitignored)
├── samples/
│   ├── demo_article.txt
│   ├── stub.mp3
│   ├── podcast_sting_intro.mp3
│   ├── podcast_sting_mid.mp3
│   └── podcast_sting_outro.mp3
└── src/
    ├── scrape.py          # URL / paste ingest
    ├── script.py          # structured episode script
    ├── tts.py             # TTS + cue assembly
    └── pipeline.py        # orchestration + badges
```

## License

Use freely for local demos and experiments. Respect third-party API terms and site policies when scraping or calling external services.
