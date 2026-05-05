# Shorts Pipeline — Full Automation with Approval Queue

A semi-autonomous YouTube Shorts pipeline. Runs on a $5 VPS, produces 1 finished video
per night, you tap ✓/✗ on your phone in the morning, approved videos auto-post on schedule.

## Architecture

```
                ┌─── nightly (cron 3am) ──────────────────────────────┐
                │                                                      │
  HN + Reddit ──┤ idea_agent ─→ script_agent ─→ asset_agent           │
                │   (Haiku)       (Sonnet)        ↓                   │
                │                              ElevenLabs (voiceover) │
                │                              Pexels    (b-roll)     │
                │                              Whisper   (captions)   │
                │                                 ↓                   │
                │                              assemble.py (FFmpeg)   │
                │                                 ↓                   │
                │                              queue (status=pending) │
                └──────────────────────────────────────────────────────┘
                                                  ↓
                ┌─── morning ──────────────────────────────────────────┐
                │ You open https://your-vps:5001/?token=...            │
                │ Watch 45s clip on phone → tap ✓ or ✗                 │
                │ Approved → status=approved                           │
                └──────────────────────────────────────────────────────┘
                                                  ↓
                ┌─── posting hours (cron hourly) ──────────────────────┐
                │ run_daily.py post                                    │
                │ Picks one approved item → YouTube Data API → posts  │
                │ Respects MAX_UPLOADS_PER_DAY                         │
                └──────────────────────────────────────────────────────┘
```

## Files

| File | What it does |
|------|--------------|
| `config.py` | All settings: niche, voice, models, API keys, schedule |
| `idea_agent.py` | Pulls trends, asks Haiku to filter to your niche |
| `script_agent.py` | Sonnet writes timed scripts with B-roll cues |
| `asset_agent.py` | ElevenLabs voiceover + Pexels B-roll + Whisper captions |
| `assemble.py` | FFmpeg → final 1080×1920 MP4 |
| `upload_agent.py` | YouTube Data API v3 upload with OAuth |
| `queue_manager.py` | Approval queue state (pending/approved/uploaded/rejected) |
| `approve_ui.py` | Mobile-friendly Flask UI for tap-to-approve |
| `run_daily.py` | Orchestrator — what cron runs |

## API keys you'll need

| Service | Cost | Get one at |
|---------|------|------------|
| Anthropic | ~$1/mo at this volume | https://console.anthropic.com |
| ElevenLabs | $5/mo Starter | https://elevenlabs.io |
| Pexels | free | https://www.pexels.com/api/ |
| YouTube Data API | free | https://console.cloud.google.com |

**Total recurring: ~$11/month** (incl. $5 VPS), under your $50 cap.

## Setup

### 1. Local development first

Get this working on your laptop before deploying. Smoke-test each piece.

```bash
git clone <wherever you put this>
cd shorts-pipeline
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
brew install ffmpeg # on macOS

# secrets
export ANTHROPIC_API_KEY="sk-ant-..."
export ELEVENLABS_API_KEY="..."
export PEXELS_API_KEY="..."
export APPROVE_UI_TOKEN="$(openssl rand -hex 16)"   # save this!
```

Edit `config.py`:
- `NICHE`, `CHANNEL_DESCRIPTION`, `VOICE_GUIDE` — your channel
- `ELEVENLABS_VOICE_ID` — pick from https://elevenlabs.io/app/voice-library

### 2. YouTube OAuth (one time, on your laptop)

1. https://console.cloud.google.com → New project
2. APIs & Services → Library → enable **YouTube Data API v3**
3. OAuth consent screen → External → add yourself as test user
4. Credentials → Create OAuth client ID → **Desktop app** → download JSON
5. Save it as `youtube_client_secret.json` in the project root
6. Run the auth flow:
   ```bash
   python upload_agent.py --auth
   ```
   Browser opens, sign in, allow. Creates `youtube_token.json`.

### 3. Smoke-test the pipeline locally

```bash
# load env var
source .env.sh
# This runs the whole thing for ONE video
python run_daily.py produce

# Check what came out
ls output/videos/      # should have an mp4
ls output/queue.json   # should have one pending item

# Open the approval UI
python approve_ui.py
# Visit http://localhost:5001/?token=$APPROVE_UI_TOKEN
# Approve a test video, then:
python run_daily.py post
```

If all that works, your pipeline is sound. Check the resulting YouTube
upload — sound, visuals, captions, on-screen text. **This is where you
discover whether the format works**, before automating it.

### 4. VPS deployment

Cheapest decent options: Hetzner CX11 (~$4/mo), DigitalOcean basic droplet ($6/mo).

```bash
# on the VPS, as a non-root user with sudo
sudo apt update && sudo apt install -y python3-venv ffmpeg git
git clone <your-repo>      # or scp the directory over
cd shorts-pipeline
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# copy your secrets file (DON'T put keys in git)
nano .env
# add:
# ANTHROPIC_API_KEY=...
# ELEVENLABS_API_KEY=...
# PEXELS_API_KEY=...
# APPROVE_UI_TOKEN=...

# copy youtube_token.json from your laptop (this is the auth artifact)
scp youtube_token.json user@vps:~/shorts-pipeline/

# also copy youtube_client_secret.json (used for refresh)
scp youtube_client_secret.json user@vps:~/shorts-pipeline/
```

### 5. Cron

```bash
crontab -e
```

Add (adjust paths and hours to your timezone):
```cron
# Load env vars
SHELL=/bin/bash
BASH_ENV=/home/you/shorts-pipeline/.env
MAILTO=you@example.com

# Produce one video at 3am
0 3 * * * cd /home/you/shorts-pipeline && ./venv/bin/python run_daily.py produce >> output/cron.log 2>&1

# Try to post hourly between 9am–6pm (will respect MAX_UPLOADS_PER_DAY)
0 9-18 * * * cd /home/you/shorts-pipeline && ./venv/bin/python run_daily.py post >> output/cron.log 2>&1
```

### 6. Approval UI access

You don't want port 5001 open to the internet. Three options, easiest first:

**Option A — Tailscale (recommended).** Install on VPS and your phone. Visit
`http://<vps-tailscale-name>:5001/?token=...` from your phone over the Tailscale
mesh. Free, no public exposure.

**Option B — Cloudflare Tunnel.** Free, gives you a public HTTPS URL with optional
Access auth. Slightly more setup.

**Option C — SSH local-forward.** From your laptop:
`ssh -L 5001:localhost:5001 user@vps` → visit http://localhost:5001. Works on
laptop, awkward on phone.

Run the approval UI as a systemd service so it stays up:

```ini
# /etc/systemd/system/shorts-approve.service
[Unit]
Description=Shorts Approval UI
After=network.target

[Service]
Type=simple
User=you
WorkingDirectory=/home/you/shorts-pipeline
EnvironmentFile=/home/you/shorts-pipeline/.env
ExecStart=/home/you/shorts-pipeline/venv/bin/python approve_ui.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now shorts-approve
```

## Cost ceiling

At 1 video/day:

| Item | Monthly |
|------|---------|
| Claude API (Haiku ideas + Sonnet scripts) | ~$1 |
| ElevenLabs Starter (30k chars ≈ 50 shorts) | $5 |
| Pexels | $0 |
| YouTube API | $0 |
| VPS | $5 |
| **Total** | **~$11** |

## Things to watch

1. **YouTube monetization policy.** Channels with auto-posted, identical-template
   AI Shorts get demonetized. You're already doing better than most by keeping
   the human approval step. Vary your hooks, occasionally splice in your own
   screen recordings instead of pure stock B-roll, and the channel reads as
   curated rather than generated.

2. **Quota.** YouTube gives you 10,000 units/day. Each upload costs 1,600.
   `MAX_UPLOADS_PER_DAY = 2` is the safe default. Above that, request a quota
   increase — Google reviews these manually and is wary of automated channels.

3. **Voiceover variety.** ElevenLabs voices can sound robotic if you use the
   same voice + same style settings on every video. Consider rotating between
   2-3 voices, or tweaking style/stability settings per video — easy to add
   to `asset_agent.py` later.

4. **Fact-check flags.** When the script agent flags something, the video
   still gets built — but you'll see the flag in the approval UI. Take those
   seriously, especially in the dev/AI-tools niche where viewers will catch
   errors instantly.

5. **First 20 videos.** Even with full automation, watch the analytics on
   your first 20 closely. Retention curve, swipe-away point, comments. Adjust
   `VOICE_GUIDE` and the script agent prompts based on what you see.

## Going from semi-auto to full auto

If after 50+ videos you trust the pipeline completely and want to remove the
approval step: in `run_daily.py`, change the `produce_one()` function so it
calls `q.approve(slug)` immediately after `q.add(...)`. That's it. The post
phase will then upload it directly.

I'd strongly suggest keeping at least the fact-check pause (`PAUSE_ON_FACT_FLAGS = True`)
even if you remove the standard approval step. The cost of one bad video posting
is much higher than the cost of occasionally pausing.
