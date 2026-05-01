"""
Daily Orchestrator
──────────────────
Runs the whole pipeline end-to-end. Two modes:

  python run_daily.py produce
    Generate ideas → write scripts → fetch assets → assemble videos →
    add to approval queue. Run this nightly (cron at 3am).

  python run_daily.py post
    For any approved item, upload it (respecting the per-day cap).
    Run this hourly during your POSTING_HOURS.

Logs to output/run.log. Exits non-zero on failure so cron's MAILTO catches it.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import config
import queue_manager as q
import script_agent
import asset_agent
import assemble
import upload_agent

# ─── LOGGING ──────────────────────────────────────────────────────

LOG_PATH = Path(config.OUTPUT_DIR) / "run.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("orchestrator")


# ─── PRODUCE: ideas → script → assets → assembled video → queue ───

def produce_one() -> bool:
    """Run the full produce pipeline for ONE video. Returns True on success."""
    log.info("─── produce: starting ───")

    if not q.has_capacity():
        log.info("queue at capacity (%d pending), skipping", config.MAX_PENDING)
        return False

    # Step 1: ideas
    log.info("step 1: generating ideas")
    r = subprocess.run([sys.executable, "idea_agent.py"], capture_output=True, text=True)
    if r.returncode != 0:
        log.error("idea_agent failed:\n%s", r.stderr)
        return False

    ideas_path = Path(config.OUTPUT_DIR) / config.IDEAS_FILE
    ideas = json.loads(ideas_path.read_text())["ideas"]
    if not ideas:
        log.warning("no ideas survived filtering, aborting")
        return False

    # Pick the highest-scoring idea we haven't already used
    existing_slugs = {i["slug"] for i in q.load()}
    chosen = None
    for idea in ideas:
        slug = script_agent.slugify(idea["title"])
        if slug not in existing_slugs:
            chosen = idea
            break
    if not chosen:
        log.info("all ideas already produced, skipping")
        return False

    log.info("chose: %s (score %d)", chosen["title"], chosen["score"])

    # Step 2: script
    log.info("step 2: writing script")
    try:
        script = script_agent.write_script(chosen)
    except Exception as e:
        log.error("script generation failed: %s", e)
        return False

    slug = script_agent.slugify(chosen["title"])
    script["_meta"] = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source_idea": chosen,
        "model": config.SCRIPT_MODEL,
    }
    scripts_dir = Path(config.OUTPUT_DIR) / config.SCRIPTS_DIR
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_path = scripts_dir / f"{slug}.json"
    script_path.write_text(json.dumps(script, indent=2))

    # Fact-check gate
    flags = script.get("fact_check_flags", [])
    if flags and config.PAUSE_ON_FACT_FLAGS:
        log.warning("fact-check flags present, will require human approval: %s", flags)
        # Still continue to assemble — the human reviews the FINISHED video

    # Step 3: assets
    log.info("step 3: fetching assets")
    try:
        asset_dir = asset_agent.process_script(script_path)
    except Exception as e:
        log.error("asset_agent failed: %s", e)
        return False

    # Step 4: assemble
    log.info("step 4: assembling video")
    try:
        video_path = assemble.assemble(asset_dir)
    except Exception as e:
        log.error("assemble failed: %s", e)
        return False

    # Step 5: enqueue for approval
    q.add(
        slug=slug,
        title=script["title"],
        description=script["description"],
        tags=script["tags"],
        video_path=str(video_path),
        fact_check_flags=flags,
    )
    log.info("queued for approval: %s", slug)
    return True


def produce(target_count: int = 1) -> None:
    made = 0
    for _ in range(target_count):
        if not q.has_capacity():
            log.info("hit MAX_PENDING (%d), stopping", config.MAX_PENDING)
            break
        if produce_one():
            made += 1
        time.sleep(2)
    log.info("produce: made %d video(s)", made)


# ─── POST: take approved items and upload ────────────────────────

def post() -> None:
    log.info("─── post: checking queue ───")
    items = q.approved_ready()
    if not items:
        log.info("nothing approved")
        return

    if not upload_agent.can_upload_today():
        log.info("daily upload cap reached (%d)", config.MAX_UPLOADS_PER_DAY)
        return

    # Upload one per run; cron schedules multiple runs across POSTING_HOURS
    item = items[0]
    log.info("uploading: %s", item["slug"])
    try:
        video_id = upload_agent.upload_video(
            video_path=Path(item["video_path"]),
            title=item["title"],
            description=item["description"],
            tags=item["tags"],
        )
        upload_agent.record_upload(video_id, item["slug"])
        q.mark_uploaded(item["slug"], video_id)
        log.info("✓ uploaded https://youtube.com/watch?v=%s", video_id)
    except SystemExit as e:
        log.error("upload failed: %s", e)
        raise


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("produce", "post"):
        sys.exit("Usage: python run_daily.py [produce|post]")
    if sys.argv[1] == "produce":
        # Default: produce 1 video per nightly run. Bump higher if you want more.
        produce(target_count=1)
    else:
        post()


if __name__ == "__main__":
    main()
