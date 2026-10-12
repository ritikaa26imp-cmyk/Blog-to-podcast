"""Article → structured podcast episode script (fixed segment format)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

import requests

MAX_SPOKEN_CHARS = 2400
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_HOST_NAME = "Alex"

PartKind = Literal[
    "cue_intro",
    "host_intro",
    "headlines",
    "cue_topic",
    "deep_dive",
    "ending",
    "cue_outro",
]

CUE_INTRO = "<<<CUE_INTRO>>>"
HOST_INTRO = "<<<HOST_INTRO>>>"
HEADLINES = "<<<HEADLINES>>>"
CUE_TOPIC = "<<<CUE_TOPIC>>>"
DEEP_DIVE = "<<<DEEP_DIVE>>>"
ENDING = "<<<ENDING>>>"
CUE_OUTRO = "<<<CUE_OUTRO>>>"

PODCAST_SYSTEM_PROMPT = """You write a short podcast episode about an article for a single TTS voice.

Output MUST use these markers exactly (markers are not spoken). Order is fixed:

<<<CUE_INTRO>>>
<<<HOST_INTRO>>>
(short host intro — required. You may say you are the host named {host}. 1–3 sentences. Tasteful.)
<<<HEADLINES>>>
(brief rundown of 2–4 main points from the article, spoken as a short list)
<<<CUE_TOPIC>>>
<<<DEEP_DIVE>>>
(deep dive on headline 1 — content-focused, not about yourself)
<<<CUE_TOPIC>>>
<<<DEEP_DIVE>>>
(deep dive on headline 2)
(optional more <<<CUE_TOPIC>>> / <<<DEEP_DIVE>>> pairs — max 4 deep dives total)
<<<ENDING>>>
(short closing for the listener)
<<<CUE_OUTRO>>>

Rules:
- Include every marker above. Use 2–4 deep dives, each preceded by <<<CUE_TOPIC>>>.
- Spoken text only under HOST_INTRO, HEADLINES, DEEP_DIVE, ENDING.
- Short sentences; commas, periods, em-dashes, ellipses for breath. Never ALL CAPS.
- Do not invent facts not in the article.
- Never put music cues in spoken text (no [music], *music*, Host: labels, etc.).
- Deep-dives stay content-focused (avoid “I think / join me” filler). Host self-reference is OK only in HOST_INTRO.
- Keep total spoken text under ~2400 characters.

Reply with the marked episode only — nothing else."""


@dataclass
class EpisodePart:
    kind: PartKind
    text: str = ""


@dataclass
class ScriptResult:
    script: str  # clean spoken transcript for UI
    provider: str  # ollama | heuristic | openai
    char_count: int
    parts: list[EpisodePart] = field(default_factory=list)
    model: Optional[str] = None
    raw_marked: str = ""


_SPEAKER_LABEL = re.compile(
    r"^\s*(?:host|narrator|guest|speaker|anchor|vo|voice[\s-]?over|"
    r"announcer|interviewer|podcaster)\s*:\s*",
    re.IGNORECASE,
)
_BRACKET_CUE = re.compile(r"\[[^\]]*\]|\[[^\]]*$|<[^>]*>")
_ASTERISK_CUE = re.compile(r"\*[^*\n]{0,80}\*")
_PAREN_SFX = re.compile(
    r"\((?:[^)]*(?:music|sfx|sound|laugh|applause|pause|silence|fade|"
    r"theme|jingle|drums|cheers|cough|clears?\s+throat)[^)]*)\)",
    re.IGNORECASE,
)
_MARKER_LINE = re.compile(
    r"^\s*<<<(?:CUE_INTRO|HOST_INTRO|HEADLINES|CUE_TOPIC|DEEP_DIVE|ENDING|CUE_OUTRO)>>>\s*$"
)


def host_name() -> str:
    return (os.getenv("PODCAST_HOST_NAME") or DEFAULT_HOST_NAME).strip() or DEFAULT_HOST_NAME


def strip_stage_directions(text: str) -> str:
    if not text:
        return ""
    cleaned_lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            cleaned_lines.append("")
            continue
        if _MARKER_LINE.match(line):
            continue
        line = _SPEAKER_LABEL.sub("", line)
        line = _BRACKET_CUE.sub("", line)
        line = _ASTERISK_CUE.sub("", line)
        line = _PAREN_SFX.sub("", line)
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^[-*•]\s+", "", line)
        line = re.sub(r"\s{2,}", " ", line).strip(" -–—\t")
        if not line or re.fullmatch(r"[\W_]+", line):
            continue
        cleaned_lines.append(line)
    out: list[str] = []
    blank = False
    for line in cleaned_lines:
        if not line:
            if not blank and out:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out).strip()


def normalize_for_speech(text: str) -> str:
    if not text:
        return ""

    def _soft_caps(m: re.Match[str]) -> str:
        word = m.group(0)
        return word if len(word) <= 3 else word.capitalize()

    text = re.sub(r"\b[A-Z]{4,}\b", _soft_caps, text)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    normalized: list[str] = []
    for para in paragraphs:
        para = re.sub(r"\s+", " ", para).strip()
        para = re.sub(r"\s*—\s*", " — ", para)
        para = re.sub(r"\s*--\s*", " — ", para)
        if para and para[-1] not in ".!?…":
            para += "."
        normalized.append(para)
    return " ".join(normalized).strip()


def prepare_spoken(text: str) -> str:
    return normalize_for_speech(strip_stage_directions(text))


def _clamp_spoken(text: str, limit: int = MAX_SPOKEN_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def parse_marked_episode(raw: str, host: str | None = None) -> list[EpisodePart]:
    """Parse marker-delimited episode into ordered parts; repair missing structure."""
    host = host or host_name()
    raw = raw.replace("\r\n", "\n")
    # Normalize marker spacing
    for marker in (
        CUE_INTRO,
        HOST_INTRO,
        HEADLINES,
        CUE_TOPIC,
        DEEP_DIVE,
        ENDING,
        CUE_OUTRO,
    ):
        raw = re.sub(rf"\s*{re.escape(marker)}\s*", f"\n{marker}\n", raw)

    tokens = re.split(
        r"\n(<<<(?:CUE_INTRO|HOST_INTRO|HEADLINES|CUE_TOPIC|DEEP_DIVE|ENDING|CUE_OUTRO)>>>)(?:\n|$)",
        raw,
    )
    # tokens: [preamble, marker, body, marker, body, ...]
    bodies: dict[str, list[str]] = {
        HOST_INTRO: [],
        HEADLINES: [],
        DEEP_DIVE: [],
        ENDING: [],
    }
    deep_dives: list[str] = []
    current: Optional[str] = None
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if tok.startswith("<<<") and tok.endswith(">>>"):
            current = tok
            continue
        if current == DEEP_DIVE:
            deep_dives.append(prepare_spoken(tok))
        elif current in bodies:
            bodies[current].append(prepare_spoken(tok))
        # ignore text under cue markers

    host_text = " ".join(bodies[HOST_INTRO]).strip()
    if not host_text:
        host_text = (
            f"Hi — I'm {host}, and you're listening to Blog to Podcast. "
            f"Here's what stood out in today's piece."
        )
    headlines = " ".join(bodies[HEADLINES]).strip()
    ending = " ".join(bodies[ENDING]).strip()
    deep_dives = [d for d in deep_dives if d]
    if not deep_dives:
        deep_dives = [
            "The article focuses on a few practical ideas worth remembering.",
            "Those points connect into a clear takeaway for how you might apply them.",
        ]
    deep_dives = deep_dives[:4]
    if len(deep_dives) < 2:
        deep_dives.append(
            "There's more detail in the original article if you want to go further."
        )

    if not headlines:
        # Build from first sentences of deep dives
        bites = []
        for i, d in enumerate(deep_dives, 1):
            first = re.split(r"(?<=[.!?])\s+", d)[0]
            bites.append(f"{i}) {first}")
        headlines = "Today's headlines. " + " ".join(bites)
    if not ending:
        ending = (
            "That's the episode. Open the original article if you want the full detail. "
            "Thanks for listening."
        )

    parts: list[EpisodePart] = [
        EpisodePart("cue_intro"),
        EpisodePart("host_intro", _clamp_spoken(host_text, 400)),
        EpisodePart("headlines", _clamp_spoken(headlines, 500)),
    ]
    for dive in deep_dives:
        parts.append(EpisodePart("cue_topic"))
        parts.append(EpisodePart("deep_dive", _clamp_spoken(dive, 700)))
    parts.append(EpisodePart("ending", _clamp_spoken(ending, 350)))
    parts.append(EpisodePart("cue_outro"))

    # Enforce total spoken budget
    spoken = sum(len(p.text) for p in parts if p.text)
    if spoken > MAX_SPOKEN_CHARS:
        # Trim deep dives preferentially
        over = spoken - MAX_SPOKEN_CHARS
        for p in parts:
            if p.kind == "deep_dive" and over > 0 and len(p.text) > 120:
                keep = max(120, len(p.text) - over)
                p.text = _clamp_spoken(p.text, keep)
                over = sum(len(x.text) for x in parts if x.text) - MAX_SPOKEN_CHARS
    return parts


def transcript_from_parts(parts: list[EpisodePart]) -> str:
    blocks = []
    labels = {
        "host_intro": "Host intro",
        "headlines": "Headlines",
        "deep_dive": "Deep dive",
        "ending": "Ending",
    }
    for p in parts:
        if p.kind in labels and p.text:
            blocks.append(f"{labels[p.kind]}\n{p.text}")
    return "\n\n".join(blocks)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 20]


def heuristic_script(title: str, body: str) -> ScriptResult:
    """Deterministic fixed-format episode when no LLM is available."""
    host = host_name()
    sents = _sentences(strip_stage_directions(body))
    topic = strip_stage_directions(title).strip() or "today's article"

    # Pick 2–3 deep-dive beats
    if len(sents) >= 6:
        chunks = [
            " ".join(sents[:2]),
            " ".join(sents[2:4]),
            " ".join(sents[4:6]),
        ]
    elif len(sents) >= 3:
        chunks = [sents[0], sents[len(sents) // 2], sents[-1]]
    elif sents:
        chunks = [sents[0], sents[-1] if len(sents) > 1 else sents[0]]
    else:
        chunks = [
            f"The piece centers on {topic}.",
            "It offers a few practical takeaways worth a closer look.",
        ]
    chunks = chunks[:4]

    headlines_bits = []
    for i, c in enumerate(chunks, 1):
        first = re.split(r"(?<=[.!?])\s+", c)[0]
        headlines_bits.append(f"{i}) {first}")

    marked = "\n".join(
        [
            CUE_INTRO,
            HOST_INTRO,
            f"Hi — I'm {host}, and you're listening to Blog to Podcast. "
            f"Today we're unpacking {topic}.",
            HEADLINES,
            "Here are the main headlines. " + " ".join(headlines_bits),
            *[
                item
                for c in chunks
                for item in (CUE_TOPIC, DEEP_DIVE, c)
            ],
            ENDING,
            "That's the episode. Dig into the original article if you want more. "
            "Thanks for listening.",
            CUE_OUTRO,
        ]
    )
    parts = parse_marked_episode(marked, host=host)
    script = transcript_from_parts(parts)
    return ScriptResult(
        script=script,
        provider="heuristic",
        char_count=sum(len(p.text) for p in parts if p.text),
        parts=parts,
        raw_marked=marked,
    )


def _user_prompt(title: str, body: str, host: str) -> str:
    return (
        f"Host name: {host}\n"
        f"Article title: {title}\n\n"
        f"Article text:\n{body[:12000]}\n\n"
        "Write the marked podcast episode now, following the required marker format."
    )


def _finalize_marked(raw: str, provider: str, model: Optional[str] = None) -> ScriptResult:
    parts = parse_marked_episode(raw)
    script = transcript_from_parts(parts)
    return ScriptResult(
        script=script,
        provider=provider,
        char_count=sum(len(p.text) for p in parts if p.text),
        parts=parts,
        model=model,
        raw_marked=raw,
    )


def _ollama_generate(
    title: str,
    body: str,
    host_addr: str,
    model: str,
    timeout: int = 120,
) -> ScriptResult:
    host_addr = host_addr.rstrip("/")
    hname = host_name()
    system = PODCAST_SYSTEM_PROMPT.format(host=hname)
    prompt = _user_prompt(title, body, hname)
    chat_url = f"{host_addr}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    try:
        resp = requests.post(chat_url, json=payload, timeout=timeout)
        if resp.status_code == 404:
            raise RuntimeError("chat endpoint missing")
        resp.raise_for_status()
        data = resp.json()
        content = (
            (data.get("message") or {}).get("content")
            or data.get("response")
            or ""
        )
    except Exception:
        gen_url = f"{host_addr}/api/generate"
        gen_payload = {
            "model": model,
            "stream": False,
            "prompt": f"{system}\n\n{prompt}",
        }
        resp = requests.post(gen_url, json=gen_payload, timeout=timeout)
        resp.raise_for_status()
        content = resp.json().get("response") or ""

    result = _finalize_marked(str(content), provider="ollama", model=model)
    if result.char_count < 80:
        raise RuntimeError("Ollama returned an empty or too-short episode")
    return result


def _openai_generate(title: str, body: str, api_key: str, model: str) -> ScriptResult:
    from openai import OpenAI

    hname = host_name()
    client = OpenAI(api_key=api_key)
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": PODCAST_SYSTEM_PROMPT.format(host=hname),
            },
            {"role": "user", "content": _user_prompt(title, body, hname)},
        ],
        temperature=0.7,
        max_tokens=1200,
    )
    content = completion.choices[0].message.content or ""
    result = _finalize_marked(content, provider="openai", model=model)
    if result.char_count < 80:
        raise RuntimeError("OpenAI returned an empty or too-short episode")
    return result


def generate_script(
    title: str,
    body: str,
    *,
    openai_key: Optional[str] = None,
    openai_model: Optional[str] = None,
    ollama_host: Optional[str] = None,
    ollama_model: Optional[str] = None,
    podcast_host_name: Optional[str] = None,
) -> tuple[ScriptResult, list[str]]:
    warnings: list[str] = []
    body = (body or "").strip()
    if not body:
        raise ValueError("Article text is empty — nothing to script.")

    if podcast_host_name and podcast_host_name.strip():
        os.environ["PODCAST_HOST_NAME"] = podcast_host_name.strip()

    if openai_key:
        model = openai_model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
        try:
            return _openai_generate(title, body, openai_key, model), warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"OpenAI failed ({exc}); trying Ollama.")

    host = ollama_host or os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
    model = ollama_model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    try:
        return _ollama_generate(title, body, host, model), warnings
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Ollama unavailable ({exc}); using heuristic script.")
        return heuristic_script(title, body), warnings
