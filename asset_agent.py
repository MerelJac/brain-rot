"""
Asset Agent
───────────
Reads a script JSON, produces all the raw assets needed to assemble a video:

  output/assets/{slug}/
    ├── voiceover.mp3        # full narration, segment-aligned
    ├── segments.json        # actual durations of each spoken segment
    ├── broll/               # one folder per segment with chosen clip
    │   ├── 01.mp4
    │   ├── 02.mp4
    │   └── ...
    └── captions.srt         # transcript-style timed captions (Whisper)

Usage:
    python asset_agent.py output/scripts/some-slug.json
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from typing import Optional

import httpx

import config

# ─── ELEVENLABS TTS ───────────────────────────────────────────────

def synthesize_voiceover(text: str, out_path: Path) -> None:
    """Stream MP3 from ElevenLabs and write to out_path."""
    if not config.ELEVENLABS_API_KEY:
        sys.exit("❌ ELEVENLABS_API_KEY not set in environment.")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text,
        "model_id": config.ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.3,         # a touch of expressiveness
            "use_speaker_boost": True,
            "speed": 1.1,         # slightly faster than default — snappier for shorts
        },
    }

    with httpx.stream("POST", url, json=body, headers=headers, timeout=60) as r:
        if r.status_code != 200:
            r.read()
            sys.exit(f"❌ ElevenLabs error {r.status_code}: {r.text}")
        with open(out_path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


def get_audio_duration(audio_path: Path) -> float:
    """Use ffprobe to read duration in seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


# ─── PEXELS B-ROLL ────────────────────────────────────────────────

def search_pexels_video(query: str, min_duration: float) -> Optional[dict]:
    """Search Pexels for a vertical video matching the query.
    Falls back to landscape if no vertical clip is long enough."""
    if not config.PEXELS_API_KEY:
        sys.exit("❌ PEXELS_API_KEY not set in environment.")

    headers = {"Authorization": config.PEXELS_API_KEY}
    # Try portrait first (best for shorts), then landscape as fallback
    for orientation in ("portrait", "landscape"):
        r = httpx.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": 15, "orientation": orientation},
            headers=headers, timeout=15,
        )
        if r.status_code != 200:
            continue
        videos = r.json().get("videos", [])
        # Prefer clips that are at least min_duration long
        candidates = [v for v in videos if v.get("duration", 0) >= min_duration]
        if not candidates and videos:
            candidates = videos  # accept anything if nothing's long enough
        for v in candidates:
            # Pick highest-res .mp4 file under 1080p (smaller = faster)
            files = sorted(
                [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"],
                key=lambda f: f.get("height") or 0,
            )
            mid = next((f for f in files if (f.get("height") or 0) >= 720), files[-1] if files else None)
            if mid:
                return {"url": mid["link"], "duration": v["duration"], "id": v["id"]}
    return None


def download(url: str, out_path: Path) -> None:
    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


# ─── WHISPER CAPTIONS ─────────────────────────────────────────────

def generate_captions(audio_path: Path, out_path: Path) -> None:
    """Use faster-whisper to produce transcript-style captions, one per natural phrase."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("❌ faster-whisper not installed. pip install faster-whisper")

    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path))

    with open(out_path, "w") as f:
        for idx, seg in enumerate(segments, 1):
            text = seg.text.strip()
            if not text:
                continue
            f.write(f"{idx}\n{_srt_time(seg.start)} --> {_srt_time(seg.end)}\n{text}\n\n")


def _srt_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─── KEYWORD EXTRACTION FOR B-ROLL SEARCH ─────────────────────────

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "as", "is", "was", "are", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should", "could",
    "may", "might", "must", "can", "this", "that", "these", "those", "showing",
    "close-up", "screen", "recording", "simple", "text", "card",
}


def cue_to_queries(cue: str) -> list[str]:
    """Return progressively simpler Pexels queries from a B-roll cue.
    Tries 3-word → 2-word → 1-word so we always find *something* related."""
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]+", cue.lower())
    keywords = [w for w in words if w not in STOPWORDS and len(w) > 2]
    queries = []
    for n in (3, 2, 1):
        if len(keywords) >= n:
            queries.append(" ".join(keywords[:n]))
    return queries or ["nature"]


# ─── MAIN ─────────────────────────────────────────────────────────

def process_script(script_path: Path) -> Path:
    script = json.loads(script_path.read_text())
    slug = script_path.stem

    asset_dir = Path(config.OUTPUT_DIR) / config.ASSETS_DIR / slug
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "broll").mkdir(exist_ok=True)

    print(f"→ Processing: {script['title']}")

    # 1. Voiceover (one continuous file — simpler than per-segment, and we use
    #    Whisper to recover timing anyway)
    full_voiceover_text = " ".join(s["voiceover"] for s in script["segments"])
    vo_path = asset_dir / "voiceover.mp3"
    if not vo_path.exists():
        print("  · ElevenLabs voiceover...")
        synthesize_voiceover(full_voiceover_text, vo_path)
    duration = get_audio_duration(vo_path)
    print(f"    voiceover: {duration:.1f}s")

    # 2. B-roll for each segment
    print("  · Pexels b-roll...")
    segments_meta = []
    for i, seg in enumerate(script["segments"], 1):
        seg_dur = seg["end_seconds"] - seg["start_seconds"]
        queries = cue_to_queries(seg["broll_cue"])
        clip_path = asset_dir / "broll" / f"{i:02d}.mp4"

        if not clip_path.exists():
            video = None
            matched_query = queries[0]
            for q in queries:
                video = search_pexels_video(q, min_duration=max(seg_dur, 3))
                if video:
                    matched_query = q
                    break
                print(f"    ⚠️  no b-roll for '{q}', trying simpler query...")
            if video:
                download(video["url"], clip_path)
                print(f"    {i:02d}. '{matched_query}' → pexels {video['id']}")
            else:
                print(f"    ❌ {i:02d}. no clip found for cue: {seg['broll_cue']!r}")

        segments_meta.append({
            "index": i,
            "section": seg["section"],
            "start": seg["start_seconds"],
            "end": seg["end_seconds"],
            "voiceover": seg["voiceover"],
            "on_screen_text": seg.get("on_screen_text", ""),
            "broll_query": queries[0],
            "broll_path": str(clip_path.relative_to(asset_dir)),
        })

    # 3. Captions via Whisper
    print("  · Whisper captions...")
    cap_path = asset_dir / "captions.srt"
    if not cap_path.exists():
        generate_captions(vo_path, cap_path)

    # 4. Metadata
    (asset_dir / "segments.json").write_text(json.dumps({
        "slug": slug,
        "title": script["title"],
        "description": script["description"],
        "tags": script["tags"],
        "duration": duration,
        "segments": segments_meta,
    }, indent=2))

    print(f"  ✓ Assets ready in {asset_dir}/")
    return asset_dir


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python asset_agent.py output/scripts/some-slug.json [more...]")
    for path in sys.argv[1:]:
        process_script(Path(path))


if __name__ == "__main__":
    main()