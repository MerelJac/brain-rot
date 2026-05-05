"""
Script Agent
────────────
Reads ideas.json, lets you pick which ideas to script (or auto-picks top N),
and writes a full 45-second script for each — with timing markers, B-roll
cues, and on-screen text suggestions ready for video assembly later.

Output: output/scripts/{slug}.json — one file per script.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic

import config


SCRIPT_SYSTEM_PROMPT = """You are a senior scriptwriter for a trivia YouTube Shorts channel.

Channel niche: {niche}

Channel description:
{channel_description}

Voice:
{voice}

You write tight, high-retention TRIVIA scripts in this exact structure:

  HOOK (0-3s)         — One sentence. The surprise itself, stated outright.
                        Make the viewer think "wait, what?" in 3 seconds.
                        Forbidden openers: "Did you know", "Here's a fun fact",
                        "Believe it or not", "Crazy fact", "You won't believe".
                        GOOD opener pattern: state the surprising thing as fact,
                        with a specific number/name. Example: "There's a creature
                        on Earth with three hearts and blue blood."
  SPECIFIC (3-15s)    — Drop the specific names, numbers, places. This is where
                        verifiability lives. NO vague claims. Include:
                        species names, place names, dates, or measurements.
  PAYOFF (15-{payoff_end}s) — The "so what" — context that makes the fact stick.
                        Why does this exist? What's the implication?
                        This is the part that gets shared.
  CTA ({payoff_end}-{duration}s)     — A LIGHT ask. "Follow for one fact a day" or similar.
                        ONE short sentence. Never desperate.

Target total duration: {duration} seconds spoken at a natural pace
(~165 words per minute = ~{word_target} words total). Be ruthless: a 30-second
script that runs 35 seconds in audio is too long. Cut adjectives.

Output ONLY valid JSON matching this schema:
{{
  "title": "string — YouTube title under 100 chars. State the surprise; no clickbait.",
  "description": "string — 2-3 sentence YouTube description with 3-5 relevant hashtags at the end",
  "tags": ["array", "of", "5-10", "youtube", "tags"],
  "segments": [
    {{
      "section": "HOOK | SPECIFIC | PAYOFF | CTA",
      "start_seconds": number,
      "end_seconds": number,
      "voiceover": "string — exactly what the narrator says (this is what gets read aloud)",
      "on_screen_text": "string — copy the voiceover text here word for word. Leave empty only for the CTA segment.",
      "broll_cue": "string — what the viewer should SEE. Descriptive, for human review. e.g. 'octopus swimming underwater, tentacles visible', 'aerial view of Mariana Trench', 'close-up of antique map'.",
      "broll_queries": ["array of 3-5 Pexels search strings, ordered from most-specific to broadest. Each must be 1-3 words that return real stock footage. Think: what generic footage actually exists on a stock site? e.g. for 'Tassili n'Ajjer rock art': ['cave paintings', 'ancient rock art', 'stone carvings', 'desert archaeology', 'desert']. For 'hippos in river': ['hippo river', 'hippo water', 'hippos africa', 'african wildlife', 'wildlife nature']. NEVER use proper nouns or place names as the only term — always ensure the last 1-2 queries are broad enough to guarantee a hit."]
    }}
  ],
  "fact_check_flags": ["array of any specific claims a human should verify before posting — be aggressive about flagging numbers, dates, and superlatives ('largest', 'oldest', 'only')"]
}}

CRITICAL:
• Voiceover sounds like a curious friend, not a documentary narrator.
• Contractions ("there's" not "there is"), short sentences.
• Every broll_cue must name a CONCRETE filmable subject. "Octopus underwater" yes;
  "mysterious creature" no.
• Flag EVERY specific number, superlative, and date in fact_check_flags. The
  human reviewer needs to verify them before publishing.
• Do not include music/SFX cues or camera directions.
"""


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", text.lower())
    s = re.sub(r"\s+", "-", s.strip())
    return s[:60]


def write_script(idea: dict) -> dict:
    """Generate a full script for one idea."""
    client = anthropic.Anthropic()

    duration = config.TARGET_DURATION_SECONDS
    word_target = int(duration * 165 / 60)  # match ElevenLabs 1.1x speed

    system = SCRIPT_SYSTEM_PROMPT.format(
        niche=config.NICHE,
        channel_description=config.CHANNEL_DESCRIPTION.strip(),
        voice=config.VOICE_GUIDE.strip(),
        duration=duration,
        payoff_end=duration - 5,
        word_target=word_target,
    )

    user_msg = f"""Write a {duration}-second trivia Short script for this idea:

Title: {idea['title']}
Hook (use as starting point, but improve if you can): {idea['hook']}
Fact summary: {idea['fact_summary']}
Domain: {idea.get('domain', 'general')}
Verify with: {idea.get('source_hint', '(no source hint)')}

Generate the script now. Remember: aggressive on fact-check flags, ruthless on word count."""

    msg = client.messages.create(
        model=config.SCRIPT_MODEL,
        max_tokens=2500,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)


def pick_ideas_interactive(ideas: list[dict]) -> list[dict]:
    """CLI prompt: which ideas should we script?"""
    print("\nIdeas available:\n")
    for i, idea in enumerate(ideas, 1):
        score = idea.get('score', 0)
        obs = idea.get('obscurity', '?')
        print(f"  {i}. [score {score} · obscurity {obs}] {idea['title']}")
        print(f"      {idea['hook'][:90]}...")
        print()

    raw = input("Which to script? (e.g. '1,3,4' or 'top3' or 'all'): ").strip().lower()

    if raw == "all":
        return ideas
    if raw.startswith("top"):
        try:
            n = int(raw[3:])
            return ideas[:n]
        except ValueError:
            pass
    try:
        nums = [int(x.strip()) for x in raw.split(",")]
        return [ideas[n - 1] for n in nums if 1 <= n <= len(ideas)]
    except (ValueError, IndexError):
        sys.exit("Bad selection. Aborting.")


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("❌ ANTHROPIC_API_KEY not set.")

    ideas_path = Path(config.OUTPUT_DIR) / config.IDEAS_FILE
    if not ideas_path.exists():
        sys.exit(f"❌ {ideas_path} not found. Run idea_agent.py first.")

    data = json.loads(ideas_path.read_text())
    ideas = data.get("ideas", [])
    if not ideas:
        sys.exit("❌ No ideas found in ideas.json.")

    chosen = pick_ideas_interactive(ideas)
    if not chosen:
        sys.exit("Nothing selected.")

    scripts_dir = Path(config.OUTPUT_DIR) / config.SCRIPTS_DIR
    scripts_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n→ Writing {len(chosen)} script(s) with {config.SCRIPT_MODEL}...\n")
    for idea in chosen:
        slug = slugify(idea["title"])
        out_path = scripts_dir / f"{slug}.json"
        print(f"  Writing: {idea['title']}")
        try:
            script = write_script(idea)
            script["_meta"] = {
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "source_idea": idea,
                "model": config.SCRIPT_MODEL,
            }
            out_path.write_text(json.dumps(script, indent=2))
            print(f"    ✓ {out_path}")
            if script.get("fact_check_flags"):
                print(f"    ⚠️  Fact-check needed: {script['fact_check_flags']}")
        except Exception as e:
            print(f"    ❌ Failed: {e}")

    print(f"\n✓ Done. Scripts in {scripts_dir}/")
    print("  Review each script before any video assembly.")


if __name__ == "__main__":
    main()