"""
Idea Agent (Trivia)
───────────────────
Generates surprising, VERIFIABLE trivia facts via Claude. Tracks every
fact we've used in output/used_facts.json so we don't repeat ourselves.

Output: output/ideas.json — a list of {title, hook, angle, score,
fact_summary, source_hint, domain}
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic

import config


USED_FACTS_FILE = Path(config.OUTPUT_DIR) / "used_facts.json"


IDEA_SYSTEM_PROMPT = """You are an idea scout for a trivia YouTube Shorts channel.

Channel niche: {niche}

Channel description:
{channel_description}

Voice:
{voice}

You will generate trivia facts that are SHORT-FORM-VIDEO READY: surprising
enough to be a strong hook, specific enough to be verifiable, and tellable
in 30 seconds.

CRITICAL RULES — read these carefully:

1. AVOID common-knowledge trivia. The internet is full of widely-repeated
   factoids that are WRONG (Napoleon's height, 10% of brain, lightning
   striking twice, taste map of tongue, Great Wall visible from space).
   Do NOT generate any "everyone knows" trivia. Aim for 8/10 obscurity.

2. AVOID vague claims. "Octopuses are intelligent" is bad. "Octopuses have
   three hearts and blue copper-based blood" is good. Force yourself to
   include specific numbers, dates, names, or places.

3. AVOID these landmine domains:
   • Body / brain "myths" (mostly wrong on the internet)
   • Psychology factoids ("we use only X% of our Y")
   • Historical quotes (most are misattributed)
   • "Fun facts about emotions"
   • Anything starting with "humans"

4. PREFER these reliable domains:
   • Biology of specific named species (with binomial name if relevant)
   • Geography with named places, named features, specific coordinates
   • Etymology of specific words from specific languages
   • Astronomy with named bodies and measured properties
   • Specific historical events with dated and named participants
   • Recent (post-2015) peer-reviewed scientific findings

5. EVERY fact must be one a reasonable person could verify with a single
   Wikipedia or primary-source lookup. Include a `source_hint` for that lookup.

Already-used facts (DO NOT repeat these or close variations):
{used_facts}

Output ONLY valid JSON matching this schema, nothing else:
{{
  "ideas": [
    {{
      "title": "string — the YouTube title (intriguing, no clickbait)",
      "hook": "string — first line of script (must surprise in 2 seconds)",
      "fact_summary": "string — the actual fact, 1-2 sentences, with specifics",
      "domain": "biology | geography | etymology | astronomy | history | other",
      "source_hint": "string — where to verify (e.g. 'Wikipedia: Mariana Trench')",
      "score": integer 1-10,
      "obscurity": integer 1-10
    }}
  ]
}}

Score 1-10 based on: hook strength, specificity, surprise factor.
Obscurity 1-10: how many regular people would already know this.
  - Aim for 7+ on obscurity. 1-4 is too common-knowledge.
  - Reject anything where you'd say "well, everyone knows that."

Generate 8 ideas. Quality > quantity. If you can't hit 8 without repeating
common-knowledge facts, return fewer."""


def load_used_facts() -> list[dict]:
    if USED_FACTS_FILE.exists():
        return json.loads(USED_FACTS_FILE.read_text())
    return []


def add_used_fact(idea: dict) -> None:
    used = load_used_facts()
    used.append({
        "title": idea["title"],
        "fact_summary": idea["fact_summary"],
        "domain": idea.get("domain", "other"),
        "used_at": datetime.utcnow().isoformat() + "Z",
    })
    USED_FACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USED_FACTS_FILE.write_text(json.dumps(used, indent=2))


def generate_ideas() -> dict:
    client = anthropic.Anthropic()

    used = load_used_facts()
    # Compact representation — only need title + summary for dedup
    if used:
        used_str = "\n".join(f"• {u['title']}: {u['fact_summary']}" for u in used[-100:])
    else:
        used_str = "(none yet — this is a fresh channel)"

    system = IDEA_SYSTEM_PROMPT.format(
        niche=config.NICHE,
        channel_description=config.CHANNEL_DESCRIPTION.strip(),
        voice=config.VOICE_GUIDE.strip(),
        used_facts=used_str,
    )

    msg = client.messages.create(
        model=config.IDEA_MODEL,
        max_tokens=2500,
        system=system,
        messages=[{
            "role": "user",
            "content": "Generate 8 trivia ideas for tomorrow's video lineup. Be ruthless about avoiding common-knowledge facts.",
        }],
    )

    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        sys.stderr.write(f"⚠️  Claude returned invalid JSON. Raw output:\n{raw}\n")
        raise


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("❌ ANTHROPIC_API_KEY not set.")

    print(f"→ Generating trivia ideas via {config.IDEA_MODEL}...")
    result = generate_ideas()

    # Sort by combined score+obscurity (we want both high)
    ideas = sorted(
        result.get("ideas", []),
        key=lambda i: i.get("score", 0) + i.get("obscurity", 0),
        reverse=True,
    )

    out = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "ideas": ideas,
    }

    Path(config.OUTPUT_DIR).mkdir(exist_ok=True)
    out_path = Path(config.OUTPUT_DIR) / config.IDEAS_FILE
    out_path.write_text(json.dumps(out, indent=2))

    print(f"\n✓ Wrote {len(ideas)} ideas to {out_path}\n")
    for i, idea in enumerate(ideas, 1):
        print(f"  {i}. [score {idea['score']} · obscurity {idea['obscurity']}] {idea['title']}")
        print(f"     {idea['hook'][:90]}")
        print(f"     verify: {idea['source_hint']}")
        print()


if __name__ == "__main__":
    main()