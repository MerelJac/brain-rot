"""
Approval Web UI
───────────────
Tiny Flask app for tapping ✓ or ✗ on pending videos from your phone.

Run on the VPS:
    python approve_ui.py
    # binds to 0.0.0.0:5001

Then either:
  • SSH tunnel:  ssh -L 5001:localhost:5001 you@vps  → open http://localhost:5001
  • Or open the VPS firewall (5001) and use a Tailscale/Cloudflare Tunnel
    — DON'T just open 5001 to the internet without auth in front of it.

Set APPROVE_UI_TOKEN env var; the UI requires ?token=... in the URL.
This is basic protection — for anything serious, put a real auth layer in front.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from flask import Flask, request, abort, send_file, redirect, url_for
except ImportError:
    sys.exit("❌ Flask not installed. pip install flask")

import queue_manager as q
import config

app = Flask(__name__)
TOKEN = os.environ.get("APPROVE_UI_TOKEN", "")


def _check_token():
    if not TOKEN:
        sys.exit("❌ Set APPROVE_UI_TOKEN env var before running approve_ui.py")
    if request.args.get("token") != TOKEN:
        abort(403)


PAGE = """<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shorts approval</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 480px;
         margin: 0 auto; padding: 16px; background: #111; color: #eee; }
  h1 { font-size: 20px; margin: 12px 0; }
  .card { background: #1c1c1c; border-radius: 12px; padding: 14px;
          margin-bottom: 18px; border: 1px solid #2a2a2a; }
  .title { font-weight: 600; margin-bottom: 8px; font-size: 16px; }
  .meta { color: #999; font-size: 13px; margin-bottom: 10px; }
  video { width: 100%; border-radius: 8px; background: black; }
  .flags { background: #3b1c1c; border-left: 3px solid #ff5555;
           padding: 8px 10px; margin: 10px 0; font-size: 13px; border-radius: 4px; }
  .desc { color: #bbb; font-size: 13px; margin: 8px 0; white-space: pre-wrap; }
  .tags { color: #6af; font-size: 12px; margin: 6px 0; }
  .actions { display: flex; gap: 10px; margin-top: 12px; }
  button { flex: 1; padding: 14px; border: 0; border-radius: 8px;
           font-size: 15px; font-weight: 600; cursor: pointer; }
  .approve { background: #22aa55; color: white; }
  .reject  { background: #aa3333; color: white; }
  .empty { text-align: center; color: #888; padding: 60px 20px; }
</style>
</head><body>
<h1>Pending: {{n}}</h1>
{% if not items %}
<div class="empty">Inbox zero. No videos to review.</div>
{% endif %}
{% for it in items %}
<div class="card">
  <div class="title">{{it.title}}</div>
  <div class="meta">slug: {{it.slug}} · created {{it.created_at[:16]}}</div>
  <video controls preload="metadata" src="/video/{{it.slug}}?token={{token}}"></video>
  {% if it.fact_check_flags %}
  <div class="flags"><b>⚠ Fact-check:</b><br>{% for f in it.fact_check_flags %}• {{f}}<br>{% endfor %}</div>
  {% endif %}
  <div class="desc">{{it.description}}</div>
  <div class="tags">{{it.tags|join(', ')}}</div>
  <form method="post" action="/decide?token={{token}}" class="actions">
    <input type="hidden" name="slug" value="{{it.slug}}">
    <button class="approve" name="action" value="approve">✓ Approve</button>
    <button class="reject"  name="action" value="reject">✗ Reject</button>
  </form>
</div>
{% endfor %}
</body></html>
"""


@app.route("/")
def index():
    _check_token()
    items = q.pending()
    from flask import render_template_string
    return render_template_string(PAGE, items=items, n=len(items), token=TOKEN)


@app.route("/video/<slug>")
def video(slug):
    _check_token()
    # Only serve files that are in the queue, never arbitrary paths
    items = q.load()
    match = next((i for i in items if i["slug"] == slug), None)
    if not match:
        abort(404)
    p = Path(match["video_path"])
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="video/mp4")


@app.route("/decide", methods=["POST"])
def decide():
    _check_token()
    slug = request.form["slug"]
    action = request.form["action"]
    if action == "approve":
        q.approve(slug)
    elif action == "reject":
        q.reject(slug)
    return redirect(url_for("index", token=TOKEN))


if __name__ == "__main__":
    if not TOKEN:
        sys.exit("❌ Set APPROVE_UI_TOKEN env var first.")
    app.run(host="0.0.0.0", port=5001)
