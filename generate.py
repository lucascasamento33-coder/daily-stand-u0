#!/usr/bin/env python3
"""
Daily Brief Generator
Runs at 7:30am EST via Render cron job.
6 sections, 18 stories, ~32 min listen.
"""

import os, re, base64, smtplib, datetime, feedparser, requests, json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from anthropic import Anthropic

# ── ENV VARS ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN      = os.environ["GITHUB_TOKEN"]
GITHUB_USER       = os.environ["GITHUB_USER"]
GITHUB_REPO       = os.environ["GITHUB_REPO"]
GMAIL_USER        = os.environ["GMAIL_USER"]
GMAIL_APP_PASS    = os.environ["GMAIL_APP_PASS"]
YOUR_EMAIL        = os.environ["YOUR_EMAIL"]

SECTION_ORDER = [
    "World News",
    "US News",
    "Economy",
    "US Real Estate (NYC Focus)",
    "Sports",
    "Basketball (NBA & College)",
]

FEEDS = {
    "World News": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.reuters.com/reuters/worldNews",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "US News": [
        "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
        "https://feeds.reuters.com/reuters/domesticNews",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.politico.com/politics-news.xml",
    ],
    "Economy": {
        "us": [
            "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
            "https://feeds.reuters.com/reuters/businessNews",
            "https://feeds.marketwatch.com/marketwatch/topstories/",
            "https://www.ft.com/rss/home/us",
        ],
        "world": [
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://feeds.reuters.com/reuters/UKbusinessNews",
            "https://www.ft.com/rss/home",
        ],
    },
    "US Real Estate (NYC Focus)": [
        "https://www.curbed.com/rss/index.xml",
        "https://therealdeal.com/new-york/feed/",
        "https://www.housingwire.com/feed/",
        "https://www.6sqft.com/feed/",
    ],
    "Sports": [],  # built dynamically by fetch_sports_headlines()
    "Basketball (NBA & College)": [
        "https://www.espn.com/espn/rss/nba/news",
        "https://sports.yahoo.com/nba/rss/",
        "https://www.espn.com/espn/rss/ncb/news",
    ],
}

SECTION_CONFIG = {
    "World News":                 {"color": "#c8390a", "tag": "WORLD"},
    "US News":                    {"color": "#1a5c8a", "tag": "US"},
    "Economy":                    {"color": "#1a6a5a", "tag": "ECONOMY"},
    "US Real Estate (NYC Focus)": {"color": "#2a7a3a", "tag": "RE · NYC"},
    "Sports":                     {"color": "#8a4a1a", "tag": "SPORTS"},
    "Basketball (NBA & College)": {"color": "#6a2a8a", "tag": "BBALL"},
}

HISTORY_FILE     = "story_history.json"
MAX_HISTORY_DAYS = 5


# ── FETCH ─────────────────────────────────────────────────────────────────────

def fetch_headlines(urls, count=8, seen_titles=None):
    """Fetch deduplicated headlines from a list of RSS feeds."""
    if seen_titles is None:
        seen_titles = set()
    items = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title   = entry.get("title", "").strip()
                summary = re.sub(r"<[^>]+>", "",
                    entry.get("summary", entry.get("description", ""))).strip()[:600]
                key = re.sub(r"[^a-z0-9]", "", title.lower())[:60]
                if title and len(title) > 10 and key not in seen_titles:
                    seen_titles.add(key)
                    items.append(f"- {title}. {summary}")
                if len(items) >= count:
                    break
        except Exception as e:
            print(f"  Feed error {url}: {e}")
        if len(items) >= count:
            break
    return items[:count]


def fetch_sports_headlines():
    """Fetch headlines from soccer, NFL, and MLB separately to guarantee variety."""
    sports_feeds = {
        "soccer": [
            "https://www.espn.com/espn/rss/soccer/news",
            "https://sports.yahoo.com/soccer/rss/",
        ],
        "NFL/football": [
            "https://www.espn.com/espn/rss/nfl/news",
            "https://sports.yahoo.com/nfl/rss/",
        ],
        "MLB/baseball": [
            "https://www.espn.com/espn/rss/mlb/news",
            "https://sports.yahoo.com/mlb/rss/",
        ],
    }
    all_items = []
    seen_titles = set()
    for sport, urls in sports_feeds.items():
        items = fetch_headlines(urls, count=4, seen_titles=seen_titles)
        print(f"  {sport}: {len(items)} headlines")
        all_items.extend(items)
    return all_items


# ── STORY HISTORY ─────────────────────────────────────────────────────────────

def load_story_history():
    url     = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{HISTORY_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        data = r.json()
        raw  = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(raw), data.get("sha")
    return {}, None


def save_story_history(history, sha=None):
    url     = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{HISTORY_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    payload = {
        "message": "Update story history",
        "content": base64.b64encode(json.dumps(history, indent=2).encode()).decode()
    }
    if sha:
        payload["sha"] = sha
    requests.put(url, headers=headers, json=payload)


def get_recent_titles(history, is_monday=False):
    today = datetime.date.today()
    cutoff = MAX_HISTORY_DAYS + (2 if is_monday else 0)
    seen = []
    for date_key, entries in history.items():
        try:
            d = datetime.date.fromisoformat(date_key)
            if (today - d).days <= cutoff:
                seen.extend(entries)
        except Exception:
            pass
    return seen  # list of human-readable titles, not normalized keys


def update_history(history, sections_data):
    today_key = datetime.date.today().isoformat()
    today_titles = []
    for stories in sections_data.values():
        for story in stories:
            today_titles.append(story["title"])  # store readable titles
    history[today_key] = today_titles
    cutoff = datetime.date.today() - datetime.timedelta(days=MAX_HISTORY_DAYS + 3)
    history = {k: v for k, v in history.items()
               if datetime.date.fromisoformat(k) >= cutoff}
    return history


# ── SUMMARIZE ─────────────────────────────────────────────────────────────────

IMPACT_NOTE = """
STORY SELECTION — IMPACT AND IMPORTANCE:
Only cover stories that genuinely matter. Apply this standard before picking any story:
- COVER: Major geopolitical events, significant policy changes, large economic moves, serious crimes or disasters with wide impact, major sports milestones, blockbuster trades, championship results, election outcomes, notable deaths of public figures.
- DO NOT COVER: Soft features, lifestyle stories, things blooming or growing somewhere, minor local events with no broader significance, celebrity gossip, weather unless catastrophic, anything a well-informed person would consider trivial.
- If a story wouldn't make the front page of a serious newspaper, skip it.
"""

ACCURACY_NOTE = """
ACCURACY RULES — CRITICAL:
- Only state facts that are directly supported by the headlines and summaries provided. Do NOT invent statistics, records, names, ages, or details not present in the source material.
- If a headline mentions a record was broken, you MUST state what the actual record is if it appears in the summary. If the summary doesn't say, do not guess — instead say the record was broken without fabricating the specifics.
- Do not describe a player's experience level (e.g. "rookie", "veteran") unless it is explicitly stated in the source material.
- Do not attribute quotes unless they appear in the source material.
"""

STORY_LENGTH_NOTE = """
STORY LENGTH — IMPORTANT:
Each story should be 8-10 sentences long. This is an audio brief for a 30+ minute commute. 
Give full context: who, what, when, where, why it matters, what happens next. 
Do not write a short paragraph. Write a full, substantive radio segment.
"""


def build_prompt(section, headlines, is_monday=False, recent_titles=None, extra_note=""):
    monday_note = ""
    if is_monday:
        monday_note = """
TODAY IS MONDAY — COVERAGE WINDOW:
Cover the most important stories from Saturday, Sunday, AND Monday morning combined.
Label weekend stories naturally ("Over the weekend...", "On Sunday...") and Monday news as current.
Do not skip major Monday morning news just because it's also a weekend recap brief.
"""

    recent_note = ""
    if recent_titles:
        titles_list = "\n".join(f"- {t}" for t in recent_titles[:25])
        recent_note = f"""
AVOID REPEAT STORIES:
The following stories have already been covered in recent days. Do NOT cover the same topic again unless something major and NEW has happened that significantly changes or advances the story:
{titles_list}
"""

    return f"""You are a professional radio news writer producing a daily audio brief for a smart, informed listener.

Section: {section}

{IMPACT_NOTE}
{ACCURACY_NOTE}
{STORY_LENGTH_NOTE}
{extra_note}
{monday_note}
{recent_note}

Today's headlines and summaries:
{chr(10).join(headlines)}

Write exactly 3 news stories. Format each one like this:

###
TITLE: The story title
The full story — 8 to 10 sentences. Conversational audio tone, like a confident radio anchor. 
Give context, key details, numbers, names, and why it matters. No filler phrases.
###

Output only the 3 stories in this format. No preamble, no extra text."""


def summarize_standard(client, section, headlines, is_monday=False, recent_titles=None):
    extra = ""

    if section == "US Real Estate (NYC Focus)":
        extra = """
REAL ESTATE AUDIENCE NOTE:
Write for a NYC residential property OWNER — not an agent or developer.
COVER: mortgage rate trends and forecasts, rent growth, neighborhood trends (up-and-coming areas, quality of life, crime trends), property tax and policy changes affecting owners, housing supply, economic factors affecting home values.
DO NOT COVER: individual home sales, broker tips, luxury condo launches, commercial real estate.
"""

    if section == "Sports":
        extra = """
SPORTS SECTION RULES:
- Cover soccer, NFL/football, and baseball ONLY. No basketball — it has its own section.
- Pick the 3 most impactful stories from across all sports. A blockbuster trade trumps a routine game recap regardless of sport.
- It is fine to have 2 stories from one sport if they are genuinely more important.
- Prioritize: trades, signings, injuries to stars, championship results, historic milestones, major upsets.
"""

    if section == "Basketball (NBA & College)":
        extra = """
BASKETBALL SECTION RULES:
- Cover NBA and college basketball.
- Prioritize: trades, major injuries, MVP race developments, playoff implications, historic performances, upsets.
- When a record is mentioned, state exactly what the record is and provide full context.
- Do not describe a player's experience level unless explicitly stated in the source material.
"""

    prompt = build_prompt(section, headlines, is_monday=is_monday,
                          recent_titles=recent_titles, extra_note=extra)

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


def summarize_economy(client, us_headlines, world_headlines, is_monday=False, recent_titles=None):
    extra = """
ECONOMY SECTION RULES:
- Story 1: US economy
- Story 2: US economy  
- Story 3: World economy
- Include specific numbers: market levels, percentage moves, rat
