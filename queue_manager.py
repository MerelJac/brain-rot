"""
Approval Queue
──────────────
Tracks the state of every video the pipeline produces:

  pending   → assembled and waiting for human approval
  approved  → cleared to upload at next scheduled slot
  uploaded  → posted to YouTube (logged with video_id)
  rejected  → human said no; kept for inspection
  expired   → sat in pending too long, auto-rejected

State lives in output/queue.json so it survives restarts.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

QUEUE_PATH = Path(config.OUTPUT_DIR) / config.QUEUE_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load() -> list[dict]:
    if QUEUE_PATH.exists():
        return json.loads(QUEUE_PATH.read_text())
    return []


def save(items: list[dict]) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(items, indent=2))


def add(slug: str, title: str, description: str, tags: list[str],
        video_path: str, fact_check_flags: list[str]) -> None:
    items = load()
    if any(i["slug"] == slug for i in items):
        return  # already queued
    items.append({
        "slug": slug,
        "status": "pending",
        "title": title,
        "description": description,
        "tags": tags,
        "video_path": video_path,
        "fact_check_flags": fact_check_flags,
        "created_at": _now(),
    })
    save(items)


def expire_old() -> None:
    """Move pending items past PENDING_EXPIRY_HOURS to expired."""
    items = load()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.PENDING_EXPIRY_HOURS)
    changed = False
    for it in items:
        if it["status"] == "pending":
            created = datetime.fromisoformat(it["created_at"])
            if created < cutoff:
                it["status"] = "expired"
                it["expired_at"] = _now()
                changed = True
    if changed:
        save(items)


def pending() -> list[dict]:
    expire_old()
    return [i for i in load() if i["status"] == "pending"]


def approved_ready() -> list[dict]:
    return [i for i in load() if i["status"] == "approved"]


def set_status(slug: str, status: str, **extra) -> bool:
    items = load()
    for it in items:
        if it["slug"] == slug:
            it["status"] = status
            it[f"{status}_at"] = _now()
            it.update(extra)
            save(items)
            return True
    return False


def approve(slug: str) -> bool:
    return set_status(slug, "approved")


def reject(slug: str) -> bool:
    return set_status(slug, "rejected")


def mark_uploaded(slug: str, video_id: str) -> bool:
    return set_status(slug, "uploaded", youtube_video_id=video_id)


def cleanup_assets(slug: str) -> None:
    """Delete large binary files for a slug after it has been uploaded."""
    import shutil
    base = Path(config.OUTPUT_DIR)

    video = base / config.VIDEOS_DIR / f"{slug}.mp4"
    if video.exists():
        video.unlink()

    asset_dir = base / config.ASSETS_DIR / slug
    for name in ("voiceover.mp3",):
        p = asset_dir / name
        if p.exists():
            p.unlink()
    broll_dir = asset_dir / "broll"
    if broll_dir.exists():
        shutil.rmtree(broll_dir)


def has_capacity() -> bool:
    """Don't pile up more pending items than MAX_PENDING."""
    return len(pending()) < config.MAX_PENDING
