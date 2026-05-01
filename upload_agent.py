"""
YouTube Upload Agent
────────────────────
Handles OAuth and uploading videos to YouTube via the Data API v3.

First-time setup (do this on your laptop, NOT the VPS):

  1. Go to https://console.cloud.google.com → create project
  2. Enable "YouTube Data API v3"
  3. APIs & Services → OAuth consent screen → External, add yourself as test user
  4. Credentials → Create OAuth Client ID → Desktop app
  5. Download JSON → save as 'youtube_client_secret.json' in this project
  6. Run `python upload_agent.py --auth` once on your laptop:
       opens browser, you sign in, creates 'youtube_token.json'
  7. Copy 'youtube_token.json' to your VPS (one-time)
       From then on, the token auto-refreshes; no more browser needed.

Then call programmatically:
    python upload_agent.py output/videos/some-slug.mp4 \
        --title "My title" --description "..." --tags tag1,tag2

Or import upload_video() from run_daily.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config

# Lazy imports — these are heavy and only the upload agent needs them
def _imports():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    return Credentials, InstalledAppFlow, Request, build, MediaFileUpload, HttpError


SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_authenticated_service(interactive_ok: bool = False):
    Credentials, InstalledAppFlow, Request, build, _, _ = _imports()

    token_path = Path(config.YOUTUBE_TOKEN_FILE)
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        pass
    elif creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    else:
        if not interactive_ok:
            sys.exit(
                "❌ No valid YouTube token. Run `python upload_agent.py --auth` "
                "on a machine with a browser, then copy youtube_token.json here."
            )
        secret_path = Path(config.YOUTUBE_CLIENT_SECRET_FILE)
        if not secret_path.exists():
            sys.exit(f"❌ {secret_path} not found. Download from Google Cloud Console.")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
        print(f"✓ Saved {token_path}")

    return build("youtube", "v3", credentials=creds)


def upload_video(video_path: Path, title: str, description: str,
                 tags: list, publish_at: Optional[str] = None,
                 privacy: Optional[str] = None) -> str:
    """Upload a video. Returns the YouTube video ID."""
    _, _, _, _, MediaFileUpload, HttpError = _imports()

    youtube = get_authenticated_service(interactive_ok=False)

    # YouTube limits: title 100 chars, description 5001, tags total 500 chars
    title = title[:100]
    if "#Shorts" not in description:
        description = description + "\n\n#Shorts"
    description = description[:5001]
    privacy = privacy or config.UPLOAD_PRIVACY

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags[:30],
            "categoryId": "28",  # Science & Technology — adjust if your niche is different
        },
        "status": {
            "privacyStatus": privacy if not publish_at else "private",
            "selfDeclaredMadeForKids": False,
        },
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at  # ISO 8601, must be future

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")

    try:
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media,
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"    upload progress: {int(status.progress() * 100)}%")
        return response["id"]
    except HttpError as e:
        sys.exit(f"❌ YouTube API error: {e}")


# ─── Quota / rate guards ──────────────────────────────────────────

UPLOAD_LOG = Path("output/upload_log.json")


def _load_log() -> list[dict]:
    if UPLOAD_LOG.exists():
        return json.loads(UPLOAD_LOG.read_text())
    return []


def _save_log(entries: list[dict]) -> None:
    UPLOAD_LOG.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_LOG.write_text(json.dumps(entries, indent=2))


def uploads_today() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for e in _load_log() if e["date"] == today)


def record_upload(video_id: str, slug: str) -> None:
    log = _load_log()
    log.append({
        "date": datetime.now(timezone.utc).date().isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "video_id": video_id,
        "slug": slug,
    })
    _save_log(log)


def can_upload_today() -> bool:
    return uploads_today() < config.MAX_UPLOADS_PER_DAY


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("video", nargs="?")
    p.add_argument("--auth", action="store_true", help="Run interactive OAuth")
    p.add_argument("--title")
    p.add_argument("--description", default="")
    p.add_argument("--tags", default="", help="comma-separated")
    p.add_argument("--privacy", choices=["public", "private", "unlisted"])
    args = p.parse_args()

    if args.auth:
        get_authenticated_service(interactive_ok=True)
        print("✓ Auth complete.")
        return

    if not args.video or not args.title:
        sys.exit("Need --video and --title (or use --auth)")

    if not can_upload_today():
        sys.exit(f"❌ Already uploaded {config.MAX_UPLOADS_PER_DAY} today.")

    vid = upload_video(
        video_path=Path(args.video),
        title=args.title,
        description=args.description,
        tags=[t.strip() for t in args.tags.split(",") if t.strip()],
        privacy=args.privacy,
    )
    record_upload(vid, Path(args.video).stem)
    print(f"✓ Uploaded: https://www.youtube.com/watch?v={vid}")


if __name__ == "__main__":
    main()
