"""
Configuration for the Shorts pipeline (Phase 2: full assembly + approval queue).
Edit this file to customize your channel.
"""
import os

# ─── CHANNEL IDENTITY ─────────────────────────────────────────────
NICHE = "verifiable, surprising trivia in science, history, geography, and language"

CHANNEL_DESCRIPTION = """
Punchy 'did you know' shorts that teach ONE specific, verifiable fact per video.
Each video opens with a surprising claim, gives the specific detail (numbers,
names, dates, places), and ends with a tiny payoff — context that makes the
fact stick. No vague factoids, no recycled myths, no 'fun facts' that turn
out to be wrong.

Sweet-spot domains:
  • Animals: specific anatomy, behavior, capabilities (with named species)
  • Geography: places that defy expectation, with specific coordinates/names
  • Language: word origins with documented etymology
  • Recent science: discoveries from peer-reviewed papers (last 5 years preferred)
  • History: specific events, dates, people — with verifiable details
"""

VOICE_GUIDE = """
- Open with the surprise, never with 'did you know' (overused — vary the hook)
- Use specific names, numbers, places — never 'scientists say' or 'studies show'
- Confident but not breathless. Avoid 'mind-blowing' and 'crazy'
- Sound like a curious friend telling you something they just read,
  not a YouTube trivia bot
- One fact per video. Don't pad with 'and another thing'
"""

TARGET_DURATION_SECONDS = 15  # Trivia hits harder short — 15s is the sweet spot

# ─── MODELS ───────────────────────────────────────────────────────
# Verified May 2026: https://docs.claude.com/en/api/overview
IDEA_MODEL = "claude-haiku-4-5-20251001"   # $1/$5 per MTok
SCRIPT_MODEL = "claude-sonnet-4-6"          # $3/$15 per MTok

# ─── ELEVENLABS (TTS) ─────────────────────────────────────────────
# Get a key at https://elevenlabs.io — Starter plan is $5/month
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
# Pick a voice from https://elevenlabs.io/app/voice-library
# Default below is "Adam" — a popular natural-sounding male voice.
# Clone your own voice in the ElevenLabs UI, then paste its voice_id here.
ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"
ELEVENLABS_MODEL = "eleven_turbo_v2_5"  # fastest + cheapest, sounds great

# ─── PEXELS (B-ROLL) ──────────────────────────────────────────────
# Get a free API key at https://www.pexels.com/api/
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")

# ─── YOUTUBE ──────────────────────────────────────────────────────
# Setup: https://developers.google.com/youtube/v3/getting-started
# 1. Create a project at console.cloud.google.com
# 2. Enable YouTube Data API v3
# 3. Create OAuth 2.0 Client ID (Desktop app type)
# 4. Download credentials JSON, save as 'youtube_client_secret.json' in project root
YOUTUBE_CLIENT_SECRET_FILE = "youtube_client_secret.json"
YOUTUBE_TOKEN_FILE = "youtube_token.json"  # auto-created on first auth

# Daily quota: 10,000 units. Each upload = 1,600 units. Cap at 2/day to be safe.
MAX_UPLOADS_PER_DAY = 2
UPLOAD_PRIVACY = "public"  # "public" | "private" | "unlisted"

# Posting schedule (24h, your VPS local time). Skipped if not approved by then.
POSTING_HOURS = [9, 17]  # 9 AM and 5 PM

# ─── PATHS ────────────────────────────────────────────────────────
OUTPUT_DIR = "output"
IDEAS_FILE = "ideas.json"
SCRIPTS_DIR = "scripts"
ASSETS_DIR = "assets"      # voiceover + b-roll per video
VIDEOS_DIR = "videos"      # final mp4s
QUEUE_FILE = "queue.json"  # approval queue state

# ─── BEHAVIOR ─────────────────────────────────────────────────────
# Stop everything if a script flags facts to check. Recommended: True.
PAUSE_ON_FACT_FLAGS = True

# How many videos to keep "pending approval" at once. Older ones expire.
MAX_PENDING = 5
PENDING_EXPIRY_HOURS = 48