"""
Video Assembler
───────────────
Pure FFmpeg, no AI. Takes an asset directory (output/assets/{slug}/) and
produces output/videos/{slug}.mp4 — vertical 1080x1920, 30fps, with:

  • B-roll clips trimmed/cropped to fit each segment's timing
  • Voiceover audio mixed in
  • Captions burned in (large, centered, with stroke)
  • On-screen text overlays per segment

Usage:
    python assemble.py output/assets/some-slug
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import config

WIDTH, HEIGHT, FPS = 1080, 1920, 30


def run(cmd: list[str], stream: bool = False, **kw) -> subprocess.CompletedProcess:
    """Run a command, raising with stderr on failure.
    If stream=True, FFmpeg progress prints live to your terminal so you can
    see whether it's actually working vs hung."""
    if stream:
        # Show ffmpeg progress (it prints to stderr). Keep stdout captured.
        r = subprocess.run(cmd, **kw)
        if r.returncode != 0:
            sys.stderr.write(f"\n❌ Command failed: {' '.join(cmd[:3])}...\n")
            raise SystemExit(1)
        return r
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.stderr.write(f"\n❌ Command failed: {' '.join(cmd[:3])}...\n")
        sys.stderr.write(r.stderr[-2000:])
        raise SystemExit(1)
    return r


def check_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        sys.exit("❌ ffmpeg/ffprobe not found. Install: apt install ffmpeg")


def prepare_segment_clip(broll_path: Path, duration: float, out_path: Path) -> None:
    """Take a b-roll clip, crop to 1080x1920, trim/loop to exact duration."""
    # Probe the source
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(broll_path)],
        capture_output=True, text=True,
    )
    src_dur = float(probe.stdout.strip()) if probe.stdout.strip() else 0

    # If source is shorter than target, loop it
    inputs = ["-stream_loop", "-1", "-i", str(broll_path)] if src_dur < duration else ["-i", str(broll_path)]

    # crop to fill 1080x1920 (vertical), scale, set fps
    vf = (
        f"scale=w='if(gt(a,{WIDTH}/{HEIGHT}),-2,{WIDTH})':"
        f"h='if(gt(a,{WIDTH}/{HEIGHT}),{HEIGHT},-2)',"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1"
    )

    run([
        "ffmpeg", "-y", *inputs, "-t", f"{duration:.3f}",
        "-vf", vf, "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(out_path),
    ])


def concat_clips(clip_paths: list[Path], out_path: Path) -> None:
    """Concatenate prepared clips losslessly using the concat demuxer."""
    list_file = out_path.parent / "concat.txt"
    list_file.write_text("\n".join(f"file '{p.absolute()}'" for p in clip_paths))
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(out_path),
    ])
    list_file.unlink()


def burn_captions_and_audio(silent_video: Path, voiceover: Path, captions: Path,
                            overlays: list[dict], out_path: Path) -> None:
    """Add audio + burn-in captions + on-screen text."""
    # Caption styling — trendy Shorts look: big, bold, yellow with thick black outline.
    # Color format is &HBBGGRR (BGR not RGB!). Yellow = &H00FFFF.
    # Using Arial (universally available) + Bold for the heavy look.
    # Alignment=2 + MarginV=900 puts captions in the upper-middle of the frame
    # (1920px tall, MarginV is from the bottom — 900 ≈ slightly above center).
    cap_style = (
        "FontName=Arial,FontSize=22,"
        "PrimaryColour=&H00FFFF&,"           # yellow text (BGR)
        "OutlineColour=&H000000&,"           # black outline
        "BorderStyle=1,Outline=4,Shadow=2,"
        "Alignment=2,MarginV=900,Bold=1"
    )

    # Build a complex filter: subtitles first, then drawtext per overlay.
    # We write each overlay's text to its own file and use textfile= to avoid
    # FFmpeg drawtext's brutal inline-text escaping rules (colons, percent signs,
    # backslashes, brackets, etc. all need different escapes inline; textfile sidesteps all of it).
    filters = [f"subtitles={captions}:force_style='{cap_style}'"]
    overlay_text_dir = out_path.parent / "_overlays"
    overlay_text_dir.mkdir(exist_ok=True)
    for i, o in enumerate(overlays):
        if not o["text"]:
            continue
        text_file = overlay_text_dir / f"overlay_{i:02d}.txt"
        text_file.write_text(o["text"])
        # textfile= path needs forward slashes and colons escaped on the path itself
        tf_path = str(text_file.absolute()).replace(":", r"\:")
        filters.append(
            f"drawtext=textfile='{tf_path}':fontsize=64:fontcolor=white:"
            f"box=1:boxcolor=black@0.55:boxborderw=24:"
            f"x=(w-text_w)/2:y=h*0.18:"
            f"enable='between(t,{o['start']:.2f},{o['end']:.2f})'"
        )

    vf = ",".join(filters)

    run([
        "ffmpeg", "-y",
        "-i", str(silent_video),
        "-i", str(voiceover),
        "-vf", vf,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(out_path),
    ], stream=True)


def assemble(asset_dir: Path) -> Path:
    check_ffmpeg()
    meta = json.loads((asset_dir / "segments.json").read_text())
    slug = meta["slug"]
    duration = meta["duration"]

    print(f"→ Assembling: {meta['title']}")
    print(f"  duration: {duration:.1f}s")

    work = asset_dir / "_work"
    work.mkdir(exist_ok=True)

    # 1. Distribute total duration across segments proportionally to script timing.
    #    The script's segment durations are estimates; the real audio length is
    #    `duration`. We scale segment lengths to sum to `duration`.
    script_total = sum(s["end"] - s["start"] for s in meta["segments"])
    scale = duration / script_total if script_total else 1
    seg_durations = [(s["end"] - s["start"]) * scale for s in meta["segments"]]

    # 2. Prepare each clip at its exact target duration
    print("  · Preparing b-roll clips...")
    prepared = []
    cursor = 0.0
    overlays = []
    for i, (seg, seg_dur) in enumerate(zip(meta["segments"], seg_durations), 1):
        src = asset_dir / seg["broll_path"]
        if not src.exists():
            print(f"    ⚠️  Missing {src}, skipping")
            continue
        dst = work / f"clip_{i:02d}.mp4"
        prepare_segment_clip(src, seg_dur, dst)
        prepared.append(dst)
        if seg.get("on_screen_text"):
            overlays.append({
                "text": seg["on_screen_text"],
                "start": cursor,
                "end": cursor + seg_dur,
            })
        cursor += seg_dur

    # 3. Concat into one silent video track
    print("  · Concatenating...")
    silent = work / "silent.mp4"
    concat_clips(prepared, silent)

    # 4. Mix in voiceover, burn captions + overlays
    print("  · Burning audio + captions + overlays...")
    videos_dir = Path(config.OUTPUT_DIR) / config.VIDEOS_DIR
    videos_dir.mkdir(parents=True, exist_ok=True)
    out_path = videos_dir / f"{slug}.mp4"

    burn_captions_and_audio(
        silent_video=silent,
        voiceover=asset_dir / "voiceover.mp3",
        captions=asset_dir / "captions.srt",
        overlays=overlays,
        out_path=out_path,
    )

    # Clean up intermediate files
    shutil.rmtree(work)
    overlay_dir = videos_dir / "_overlays"
    if overlay_dir.exists():
        shutil.rmtree(overlay_dir)

    print(f"  ✓ {out_path}")
    return out_path


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python assemble.py output/assets/some-slug [more...]")
    for path in sys.argv[1:]:
        assemble(Path(path))


if __name__ == "__main__":
    main()