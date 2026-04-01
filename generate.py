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

# ââ ENV VARS ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
OPENAI_API_KEY    = os.environ["OPENAI_API_KEY"]
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
    "US Stocks",
    "US Real Estate (NYC Focus)",
    "Sports",
    "Basketball (NBA & College)",
]

FEEDS = {
    "World News": {
        "latin_america": [
            "https://feeds.reuters.com/reuters/worldNews",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
        ],
        "europe": [
            "https://feeds.reuters.com/reuters/worldNews",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        ],
        "asia": [
            "https://feeds.reuters.com/reuters/worldNews",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
        ],
        "middle_east": [
            "https://www.aljazeera.com/xml/rss/all.xml",
            "https://feeds.reuters.com/reuters/worldNews",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        ],
    },
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
        "https://therealdeal.com/miami/feed/",
        "https://therealdeal.com/new-jersey/feed/",
    ],
    "US Stocks": [
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        "https://feeds.reuters.com/reuters/businessNews",
        "https://rss.nytimes.com/services/xml/rss/nyt/Markets.xml",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    ],
    "Sports": [],  # built dynamically by fetch_sports_headlines()
    "Basketball (NBA & College)": [
        "https://www.espn.com/espn/rss/nba/news",
        "https://sports.yahoo.com/nba/rss/",
        "https://www.espn.com/espn/rss/ncb/news",
        "https://www.cbssports.com/rss/headlines/nba/",
        "https://www.cbssports.com/rss/headlines/college-basketball/",
        "https://bleacherreport.com/articles/feed?tag_id=19",
    ],
}

SECTION_CONFIG = {
    "World News":                 {"color": "#c8390a", "tag": "WORLD"},
    "US News":                    {"color": "#1a5c8a", "tag": "US"},
    "Economy":                    {"color": "#1a6a5a", "tag": "ECONOMY"},
    "US Real Estate (NYC Focus)": {"color": "#2a7a3a", "tag": "RE Â· NYC"},
    "US Stocks":                  {"color": "#1a4a8a", "tag": "STOCKS"},
    "Sports":                     {"color": "#8a4a1a", "tag": "SPORTS"},
    "Basketball (NBA & College)": {"color": "#6a2a8a", "tag": "BBALL"},
}

HISTORY_FILE     = "story_history.json"
MAX_HISTORY_DAYS = 7


# ââ FETCH âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def fetch_nyc_weather():
    """Fetch current NYC weather from wttr.in and return a natural spoken intro."""
    try:
        r = requests.get(
            "https://wttr.in/New+York+City?format=j1",
            timeout=10,
            headers={"User-Agent": "DailyBriefBot/1.0"}
        )
        r.raise_for_status()
        data = r.json()
        current = data["current_condition"][0]
        weather_desc = current["weatherDesc"][0]["value"]
        temp_f       = current["temp_F"]
        feels_like_f = current["FeelsLikeF"]
        humidity     = current["humidity"]

        # Today's high/low from first forecast day
        today_fc  = data["weather"][0]
        high_f    = today_fc["maxtempF"]
        low_f     = today_fc["mintempF"]

        # Hourly chance of rain â take max across daylight hours
        hourly = today_fc.get("hourly", [])
        rain_chances = [int(h.get("chanceofrain", 0)) for h in hourly]
        max_rain = max(rain_chances) if rain_chances else 0

        rain_note = ""
        if max_rain >= 60:
            rain_note = " Bring an umbrella â there's a good chance of rain today."
        elif max_rain >= 30:
            rain_note = " There's a slight chance of rain, so keep that in mind."

        script = (
            f"Good morning. Here's your New York City weather. "
            f"It's currently {temp_f} degrees and {weather_desc.lower()}, "
            f"feeling like {feels_like_f}. "
            f"Today's high will be {high_f}, with a low of {low_f}."
            f"{rain_note} "
            f"Now, here's what's happening in the world."
        )
        print(f"  â NYC weather fetched ({temp_f}Â°F, {weather_desc})")
        return script
    except Exception as e:
        print(f"  â  Weather fetch failed: {e}")
        return "Good morning. Let's get into today's news."


def fetch_headlines(urls, count=8, seen_titles=None, max_age_hours=40):
    """Fetch deduplicated headlines, skipping stories older than max_age_hours."""
    import time, calendar
    if seen_titles is None:
        seen_titles = set()
    cutoff = time.time() - (max_age_hours * 3600)
    items = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title   = entry.get("title", "").strip()
                summary = re.sub(r"<[^>]+>", "",
                    entry.get("summary", entry.get("description", ""))).strip()[:600]
                key = re.sub(r"[^a-z0-9]", "", title.lower())[:60]
                # Skip stories older than max_age_hours if date is available
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published:
                    if calendar.timegm(published) < cutoff:
                        continue
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


# ââ STORY HISTORY âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

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


# ââ SUMMARIZE âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

IMPACT_NOTE = """
STORY SELECTION â IMPACT AND IMPORTANCE:
Only cover stories that genuinely matter. Apply this standard before picking any story:
- COVER: Major geopolitical events, significant policy changes, large economic moves, serious crimes or disasters with wide impact, major sports milestones, blockbuster trades, championship results, election outcomes, notable deaths of public figures.
- DO NOT COVER: Soft features, lifestyle stories, things blooming or growing somewhere, minor local events with no broader significance, celebrity gossip, weather unless catastrophic, anything a well-informed person would consider trivial.
- If a story wouldn't make the front page of a serious newspaper, skip it.
- VARIETY RULE: Every story in this section MUST cover a completely different topic, event, entity, and subject. No two stories can share the same person, team, company, country, or event â even from different angles.
- SELF-CHECK â MANDATORY: After drafting all stories, re-read them together. If any two stories are about the same underlying event or subject, discard the weaker one and replace it with something entirely different before submitting output.
- NO-REPEAT ACROSS DAYS: The recent story list below reflects the last 7 days. Do not revisit any topic, person, team, or event that appeared recently unless something fundamentally new has happened (e.g. a verdict was reached, a deal was signed, a conflict escalated significantly).
"""

ACCURACY_NOTE = """
ACCURACY RULES â CRITICAL:
- Only state facts that are directly supported by the headlines and summaries provided. Do NOT invent statistics, records, names, ages, or details not present in the source material.
- If a headline mentions a record was broken, you MUST state what the actual record is if it appears in the summary. If the summary doesn't say, do not guess â instead say the record was broken without fabricating the specifics.
- Do not describe a player's experience level (e.g. "rookie", "veteran") unless it is explicitly stated in the source material.
- Do not attribute quotes unless they appear in the source material.
"""

STORY_LENGTH_NOTE = """
STORY LENGTH AND TONE â IMPORTANT:
Each story should be 8-10 sentences long. This is an audio brief for a commute â write for the ear, not the eye.
- SOUND HUMAN: Use contractions (it's, there's, we're, that's). Vary sentence length â mix short punchy sentences with longer ones.
- LEAD STRONG: Open with the most compelling angle, not a dry summary of facts.
- ACTIVE VOICE: "The Fed raised rates" not "Rates were raised by the Fed."
- SIGNPOST: Use a variety of natural transitions â but NEVER repeat the same signpost phrase across stories in this section or across the entire brief. Banned overused phrases: "Here's why this matters", "What makes this significant", "The bottom line", "worth noting", "make no mistake", "at the end of the day". Find a fresh, specific angle for every story.
- CONVERSATIONAL: Write how a confident, well-informed radio anchor actually speaks â not how a press release reads.
- Give full context: who, what, when, where, why it matters, what happens next.
- NO CATCHLINES OR REPEATED OPENERS: Each story must begin with a completely unique construction. Never open two stories the same way. Banned opening patterns: "It's official", "A major development", "For the first time", "In a major", "In what could be", "Officials say", "Authorities announced". Start each story with the most gripping specific fact.
"""

BALANCE_NOTE = """
POLITICAL BALANCE â MANDATORY:
Every story involving politics, policy, government, or social issues MUST be reported with strict neutrality.
- Present facts only. Do not editorialize, imply approval or disapproval, or use loaded language.
- If a policy or decision is controversial, briefly note that it has supporters and critics without favoring either side.
- Do not characterize politicians, parties, or movements positively or negatively beyond what the source material states as fact.
- Use neutral verbs: "said", "announced", "signed", "proposed" â not "claimed", "admitted", "pushed through", "slammed".
- If a story only has one political perspective in the source material, present it as such without amplifying it.
- This applies equally regardless of political party, ideology, or country.
- Do not select stories because they reflect well or poorly on any political figure or party. Select purely on newsworthiness.
- Do not use adjectives that imply a value judgment about a politician or policy (e.g. "controversial", "radical", "extreme", "sensible", "landmark") unless directly quoted from a neutral source.
"""


# Stories per section
SECTION_STORY_COUNTS = {
    "World News": 4,
    "US News": 3,
    "Economy": 3,
    "US Stocks": 2,
    "US Real Estate (NYC Focus)": 2,
    "Sports": 2,
    "Basketball (NBA & College)": 2,
}

def build_prompt(section, headlines, is_monday=False, recent_titles=None, extra_note="", n_stories=3):
    n = SECTION_STORY_COUNTS.get(section, 3)
    monday_note = ""
    if is_monday:
        monday_note = """
TODAY IS MONDAY â COVERAGE WINDOW:
Cover the most important stories from Saturday, Sunday, AND Monday morning combined.
Label weekend stories naturally ("Over the weekend...", "On Sunday...") and Monday news as current.
Do not skip major Monday morning news just because it's also a weekend recap brief.
"""

    recent_note = ""
    if recent_titles:
        titles_list = "\n".join(f"- {t}" for t in recent_titles[:25])
        recent_note = f"""
AVOID REPEAT STORIES â CRITICAL:
The following stories were already covered in recent days. DO NOT cover the same topic, player, team, or event again â even if the headline is worded differently.
Ask yourself: "Is this essentially the same story?" If yes, skip it entirely.
Only cover a previously-covered topic if something genuinely NEW and significant has changed (e.g. a trade was completed vs. just rumored, a player returned vs. was injured).
Recent stories to avoid repeating:
{titles_list}
"""

    return f"""You are a professional radio news writer producing a daily audio brief for a smart, informed listener.

Section: {section}

{IMPACT_NOTE}
{ACCURACY_NOTE}
{STORY_LENGTH_NOTE}
{BALANCE_NOTE}
{extra_note}
{monday_note}
{recent_note}

Today's headlines and summaries:
{chr(10).join(headlines)}

STEP 1 â RANK BY IMPORTANCE: Before writing anything, mentally rank all headlines by newsworthiness. Trades, signings, injuries to stars, championship outcomes, historic milestones, and major policy changes rank highest. Routine game recaps, minor roster moves, and repeated topics rank lowest.
STEP 2 â ELIMINATE REPEATS: Cross off any story that matches a topic already covered recently (listed above).
STEP 3 â WRITE THE TOP {n}: Write the {n} highest-ranked, non-repeated stories.

Format each story like this:

###
TITLE: The story title
The full story â 8 to 10 sentences. Conversational audio tone, like a real radio news anchor â not a robot. Use contractions (it's, there's, we're), vary your sentence length, lead with a compelling hook, use active voice, and signpost why it matters to the listener. Write how a confident human anchor actually speaks.
###

Output only the {n} stories in this format. No preamble, no extra text."""


def summarize_world_news(client, la_headlines, eu_headlines, asia_headlines, me_headlines,
                         is_monday=False, recent_titles=None):
    """Generate one story per global region for World News."""
    regions = [
        ("Latin America", "Cover ONE story from Latin America â Mexico, Central America, Caribbean, or South America. This story must be set in that region. Do not cover Europe, Asia, or the Middle East here."),
        ("Europe", "Cover ONE story from Europe â EU, UK, France, Germany, Eastern Europe, Ukraine, Russia (west of Urals). This story must be set in that region. Do not cover Latin America, Asia, or the Middle East here."),
        ("Asia and Russia/Central Asia", "Cover ONE story from Asia, Russia, or Central Asia â China, India, Japan, Korea, Southeast Asia, Australia, Russia, Kazakhstan, Afghanistan, Pakistan. This story must be set in that region. Do not cover Europe, Latin America, or the Middle East here."),
        ("Middle East", "Cover ONE story from the Middle East â Israel, Gaza, Iran, Saudi Arabia, Turkey, UAE, Syria, Iraq, Yemen. This story must be set in that region. Do not cover Europe, Asia, or Latin America here."),
    ]
    headline_sets = [la_headlines, eu_headlines, asia_headlines, me_headlines]

    all_stories_raw = []
    for (region_name, region_instruction), headlines in zip(regions, headline_sets):
        if not headlines:
            print(f"  â  No headlines for {region_name}, skipping.")
            continue

        monday_note = ""
        if is_monday:
            monday_note = """
TODAY IS MONDAY â COVERAGE WINDOW:
Cover the most important stories from Saturday, Sunday, AND Monday morning combined.
Label weekend stories naturally and Monday news as current.
"""
        recent_note = ""
        if recent_titles:
            titles_list = "\n".join(f"- {t}" for t in recent_titles[:30])
            recent_note = f"""
AVOID REPEAT STORIES â CRITICAL (7-DAY WINDOW):
Do not cover any topic, country, leader, or event that appeared in the recent story list below.
Only revisit if something fundamentally new has happened.
Recent stories to avoid:
{titles_list}
"""

        prompt = f"""You are a professional radio news writer producing a daily audio brief.

REGION: {region_name}
{region_instruction}

{IMPACT_NOTE}
{ACCURACY_NOTE}
{STORY_LENGTH_NOTE}
{BALANCE_NOTE}
{monday_note}
{recent_note}

Today's headlines from this region:
{chr(10).join(headlines)}

Write exactly ONE story about the single most important event from {region_name} today.
The story must be geographically set in {region_name} â no exceptions.

Format:
###
TITLE: The story title
The full story â 8 to 10 sentences. Conversational audio tone, no robots, no catchlines.
###

Output only the story in this format. No preamble."""

        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}]
        )
        all_stories_raw.append(msg.content[0].text.strip())
        print(f"  â {region_name} story written")

    return "\n\n".join(all_stories_raw)


def summarize_standard(client, section, headlines, is_monday=False, recent_titles=None):
    extra = ""
    n_stories = SECTION_STORY_COUNTS.get(section, 3)

    if section == "US Real Estate (NYC Focus)":
        extra = """
REAL ESTATE AUDIENCE NOTE:
Write for a residential property OWNER â not an agent or developer.
PRIMARY FOCUS: NYC (Manhattan, Brooklyn, Queens, Bronx, Staten Island). If there is not enough fresh NYC news, expand to cover Miami or New Jersey real estate markets.
COVER: mortgage rate trends and forecasts, rent growth, neighborhood trends (up-and-coming areas, quality of life, crime trends), property tax and policy changes affecting owners, housing supply, economic factors affecting home values, regional market comparisons.
DO NOT COVER: individual home sales, broker tips, luxury condo launches, commercial real estate.
If covering Miami or NJ, clearly note which market you are discussing.
"""

    if section == "US Stocks":
        extra = """
US STOCKS SECTION RULES:
- Cover US stock market news ONLY.
- COVER: Major index moves (S&P 500, Dow, Nasdaq), significant earnings results, big individual stock surges or crashes, sector-wide moves, analyst upgrades/downgrades with major impact, IPOs, Fed decisions as they affect markets.
- DO NOT COVER: general macro economy, inflation, jobs data â those belong in the Economy section.
- Always give context: what moved, by how much, and why it matters to an investor.
"""

    if section == "Sports":
        extra = """
SPORTS SECTION RULES:
- Cover soccer, NFL/football, and baseball ONLY. No basketball â it has its own section.
- Pick the 2 most impactful stories from across all sports. A blockbuster trade trumps a routine game recap regardless of sport.
- It is fine to have both stories from one sport if they are genuinely more important.
- Prioritize: trades, signings, injuries to stars, championship results, historic milestones, major upsets.
"""

    if section == "Basketball (NBA & College)":
        extra = """
BASKETBALL SECTION RULES:
- Cover NBA and college basketball.
- RANK stories in this order of importance: trades/signings, major injuries to star players, MVP race shifts, playoff standings changes, historic individual performances, major upsets. Routine game recaps are lowest priority.
- If a routine game recap is the only option for a story, skip it and find a more impactful angle.
- When a record is mentioned, state exactly what the record is and provide full context.
- Do not describe a player's experience level unless explicitly stated in the source material.
- SELF-CHECK MANDATORY: After drafting both stories, re-read them. If they cover the same player, the same team, or the same game from different angles, replace the less important one with a completely different story before submitting.
- Each story must feature a different team or player as the primary subject.
"""

    if section == "US Stocks":
        extra = """
US STOCKS SECTION RULES:
- Cover US stock market performance only. Do NOT cover general macro economy â that is in the Economy section.
- COVER: Major index moves (S&P 500, Dow, Nasdaq) with specific numbers, big earnings beats/misses, major individual stock surges or crashes, sector-wide moves, Fed decisions that moved markets.
- Always include specific numbers: index levels, percentage moves, price changes.
- Explain what the moves mean for ordinary investors.
"""

    prompt = build_prompt(section, headlines, is_monday=is_monday,
                          recent_titles=recent_titles, extra_note=extra, n_stories=n_stories)

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()



def summarize_world_news(client, la_headlines, eu_headlines, as_headlines, me_headlines,
                         is_monday=False, recent_titles=None):
    """Generate 4 world news stories â one per region."""
    extra = """
WORLD NEWS REGION RULES â CRITICAL:
You must produce exactly 4 stories, one from each of these regions:
- Story 1: LATIN AMERICA (Mexico, Central America, Caribbean, South America)
- Story 2: EUROPE (EU, UK, Ukraine, Russia from a European angle, NATO)
- Story 3: ASIA & RUSSIA/CENTRAL ASIA (China, India, Japan, Korea, Southeast Asia, Australia, Russia, Central Asia, Afghanistan, Pakistan)
- Story 4: MIDDLE EAST (Israel, Gaza, Iran, Saudi Arabia, Turkey, UAE, Syria, Iraq, Yemen)

Each story MUST come from its assigned region. Do not swap regions or skip one.
Label each story only by its headline â do not include region labels in the output.
"""
    all_headlines = (
        ["--- LATIN AMERICA ---"] + la_headlines +
        ["--- EUROPE ---"]        + eu_headlines +
        ["--- ASIA & RUSSIA ---"] + as_headlines +
        ["--- MIDDLE EAST ---"]   + me_headlines
    )
    prompt = build_prompt("World News", all_headlines, is_monday=is_monday,
                          recent_titles=recent_titles, extra_note=extra, n_stories=4)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


def summarize_economy(client, us_headlines, world_headlines, is_monday=False, recent_titles=None):
    extra = """
ECONOMY SECTION RULES:
- Story 1: US macro economy (Fed policy, jobs, inflation, GDP, consumer spending)
- Story 2: US macro economy (second most important US economic story)
- Story 3: World economy (international markets, trade, foreign economies)
- Include specific numbers: rate changes, GDP figures, jobs numbers, inflation percentages.
- Explain what the data means for ordinary people, not just markets.
- Do NOT cover stock market performance or individual stocks â that is covered in the Stocks section.

- Do NOT cover UK/London housing, mortgages, or property markets. The listener does not care about British real estate. Skip any headline about UK home prices, London rents, Bank of England mortgage policy, etc.
- CROSS-SECTION OVERLAP BAN: Do NOT cover a topic that is already naturally covered by another section. Specifically: gas prices, oil prices, and energy costs should appear in ONLY ONE section across the entire brief. If gas/oil prices are a stock market story, cover them there not here. If they are a consumer economy story, cover them here but only once.
"""
    all_headlines = us_headlines + ["--- WORLD ECONOMY ---"] + world_headlines
    prompt = build_prompt("Economy", all_headlines, is_monday=is_monday,
                          recent_titles=recent_titles, extra_note=extra, n_stories=3)

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


def parse_stories(raw, section):
    n = SECTION_STORY_COUNTS.get(section, 3)
    stories = []
    blocks = re.findall(r"###\s*(.*?)\s*###", raw, re.DOTALL)
    for block in blocks:
        block = block.strip()
        title_match = re.search(r"TITLE:\s*(.+)", block)
        title = title_match.group(1).strip() if title_match else f"{section} Update"
        body  = re.sub(r"TITLE:.+\n?", "", block).strip()
        if body:
            stories.append({"title": title, "body": body})
    return stories[:n]


# ââ BUILD HTML ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def esc(s):
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
             .replace('"',"&quot;").replace("'","&#39;"))

def build_html(sections_data, date_str, brief_label=None):
    total    = sum(len(v) for v in sections_data.values())
    est_mins = round(total * 2.5 / 1.1)  # ~2.5 min per story at 1.1x speed

    cards_html = ""
    idx = 0
    for section in SECTION_ORDER:
        stories = sections_data.get(section, [])
        if not stories:
            continue
        cfg = SECTION_CONFIG[section]
        cards_html += f'<div class="sh"><span class="sl" style="background:{cfg["color"]}">{cfg["tag"]}</span><div class="sln"></div></div><div class="sl-list">'
        for story in stories:
            speech = esc(f"{cfg['tag']}. {story['title']}. {story['body']}")
            cards_html += f'''<div class="card" data-idx="{idx}" data-speech="{speech}" onclick="playStory({idx})">
<div class="kicker"><span class="num">{str(idx+1).zfill(2)}</span><span class="stag" style="background:{cfg["color"]}">{cfg["tag"]}</span></div>
<div class="stitle">{esc(story["title"])}</div>
<div class="sbody">{esc(story["body"])}</div>
<div class="bars"><span></span><span></span><span></span><span></span><span></span></div>
</div>'''
            idx += 1
        cards_html += "</div>"

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Daily Brief Â· {date_str}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Lora:ital,wght@0,400;0,600;1,400&family=DM+Mono:wght@300;400;500&display=swap');
:root{{--bg:#f5f0e8;--ink:#1a1410;--muted:#7a7060;--dim:#c8c0b0;--rule:#d8d0c0;--accent:#c8390a;--card:#ede8e0}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--ink);font-family:'Lora',Georgia,serif;padding-bottom:130px}}
.mast{{border-bottom:3px double var(--ink);padding:16px 20px 12px;text-align:center}}
.mast-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.meta,.badge{{font-family:'DM Mono',monospace;font-size:9px;letter-spacing:.12em;text-transform:uppercase}}
.meta{{color:var(--muted)}}.badge{{color:#2a7a3a;border:1px solid #2a7a3a;padding:2px 8px}}
.logo{{font-family:'Bebas Neue',sans-serif;font-size:clamp(42px,12vw,72px);letter-spacing:.04em;line-height:1}}
.logo span{{color:var(--accent)}}
.hrule{{height:1px;background:var(--ink);margin:8px 0 6px}}
.tagline{{font-family:'Lora',serif;font-style:italic;font-size:11px;color:var(--muted)}}
.stats{{display:flex;border-bottom:1px solid var(--rule);font-family:'DM Mono',monospace;font-size:10px;color:var(--muted)}}
.stat{{flex:1;padding:8px 12px;border-right:1px solid var(--rule);text-align:center}}
.stat:last-child{{border-right:none}}
.sv{{font-size:18px;color:var(--ink);display:block;font-weight:500}}
.pa-wrap{{padding:14px 20px;border-bottom:1px solid var(--rule)}}
.pa{{width:100%;display:flex;align-items:center;justify-content:center;gap:10px;background:var(--ink);color:var(--bg);border:none;padding:14px;font-family:'Bebas Neue',sans-serif;font-size:18px;letter-spacing:.12em;cursor:pointer;transition:background .15s}}
.pa:hover{{background:var(--accent)}}
.sh{{display:flex;align-items:center;padding:0 20px;margin-top:24px}}
.sl{{font-family:'Bebas Neue',sans-serif;font-size:11px;letter-spacing:.25em;color:#fff;padding:3px 10px}}
.sln{{flex:1;height:2px;background:var(--rule);margin-left:10px}}
.sl-list{{padding:0 20px}}
.card{{border-bottom:1px solid var(--rule);padding:14px 0;cursor:pointer;position:relative;transition:background .15s}}
.card:hover,.card.active{{background:var(--card);margin:0 -20px;padding:14px 20px}}
.card.active::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--accent)}}
.kicker{{display:flex;align-items:center;gap:8px;margin-bottom:5px}}
.num{{font-family:'DM Mono',monospace;font-size:9px;color:var(--dim)}}
.stag{{font-family:'DM Mono',monospace;font-size:8px;letter-spacing:.15em;text-transform:uppercase;padding:1px 6px;color:#fff;border-radius:1px}}
.stitle{{font-family:'Lora',serif;font-size:15px;font-weight:600;line-height:1.35;color:var(--ink);margin-bottom:5px}}
.sbody{{font-size:13px;line-height:1.65;color:var(--muted)}}
.bars{{display:none;align-items:flex-end;gap:2px;height:14px;margin-top:8px}}
.card.active .bars{{display:flex}}
.bars span{{display:block;width:3px;border-radius:1px;background:var(--accent);animation:bw .8s ease-in-out infinite}}
.bars span:nth-child(1){{height:6px}}.bars span:nth-child(2){{height:12px;animation-delay:.1s}}
.bars span:nth-child(3){{height:8px;animation-delay:.2s}}.bars span:nth-child(4){{height:14px;animation-delay:.05s}}
.bars span:nth-child(5){{height:5px;animation-delay:.15s}}
@keyframes bw{{0%,100%{{transform:scaleY(.4);opacity:.5}}50%{{transform:scaleY(1);opacity:1}}}}
.footer{{padding:20px;font-family:'DM Mono',monospace;font-size:9px;color:var(--dim);border-top:1px solid var(--rule);margin-top:20px;text-align:center;line-height:1.8}}
.player{{position:fixed;bottom:0;left:0;right:0;background:var(--ink);border-top:3px solid var(--accent);padding:10px 16px 20px;z-index:100}}
.pnow{{font-family:'DM Mono',monospace;font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:8px;display:flex;justify-content:space-between;align-items:center}}
.pnow-t{{color:#f5f0e8;font-size:10px;flex:1;margin-left:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.pctrl{{display:flex;align-items:center;gap:10px}}
.pbtn{{width:42px;height:42px;border-radius:50%;background:var(--accent);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0;transition:background .15s}}
.pbtn:hover{{background:#e04010}}.pbtn:active{{transform:scale(.95)}}.pbtn svg{{fill:white}}
.pskip{{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);cursor:pointer;display:flex;align-items:center;justify-content:center}}
.pskip svg{{fill:rgba(255,255,255,.6)}}
.pw{{flex:1}}
.pt{{width:100%;height:3px;background:rgba(255,255,255,.12);border-radius:2px;cursor:pointer;margin-bottom:4px}}
.pf{{height:100%;background:var(--accent);border-radius:2px;width:0%;transition:width .5s linear}}
.tr{{display:flex;justify-content:space-between;font-family:'DM Mono',monospace;font-size:9px;color:rgba(255,255,255,.35)}}
.spd{{font-family:'DM Mono',monospace;font-size:10px;color:rgba(255,255,255,.5);background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);padding:5px 8px;cursor:pointer;border-radius:2px}}
</style>
</head>
<body>
<div class="mast">
  <div class="mast-top">
    <span class="meta">{date_str.upper()}</span>
    <span class="badge">â AI GENERATED</span>
  </div>
  <div class="logo">Daily <span>Brief</span></div>
  <div class="hrule"></div>
  <div class="tagline">{brief_label or date_str} Â· {total} stories Â· ~{est_mins} minutes</div>
</div>
<div class="stats">
  <div class="stat"><span class="sv">{total}</span>Stories</div>
  <div class="stat"><span class="sv">7</span>Sections</div>
  <div class="stat"><span class="sv">~{est_mins}m</span>Drive</div>
</div>
<div class="pa-wrap">
  <button class="pa" onclick="startAll()">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M8 5v14l11-7z"/></svg>
    PLAY ALL STORIES
  </button>
</div>
{cards_html}
<div class="footer">
  Generated by Claude AI Â· Reuters Â· NYT Â· BBC Â· FT Â· MarketWatch Â· ESPN Â· The Real Deal Â· HousingWire<br>
  {date_str} Â· Your Daily Brief
</div>
<div class="player">
  <div class="pnow"><span>Now Playing</span><span class="pnow-t" id="nt">Tap Play All to begin</span></div>
  <div class="pctrl">
    <button class="pbtn" onclick="togglePlay()"><svg id="pi" width="20" height="20" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></button>
    <button class="pskip" onclick="skipNext()"><svg width="16" height="16" viewBox="0 0 24 24"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg></button>
    <div class="pw">
      <div class="pt" onclick="seek(event)"><div class="pf" id="pf"></div></div>
      <div class="tr"><span id="tc">0:00</span><span id="tt">0:00</span></div>
    </div>
    <button class="spd" id="sb" onclick="cycleSpd()">1.1Ã</button>
  </div>
</div>
<script>
const cards=Array.from(document.querySelectorAll('.card'));
const stories=cards.map(c=>({{title:c.querySelector('.stitle').textContent,speech:c.dataset.speech}}));
let cur=-1,going=false,paused=false,allMode=false,si=2,utt=null,dur=0,el=0,ts=null,tid=null;
const spds=[0.85,1,1.1,1.2,1.5,1.75],slab=['0.85Ã','1Ã','1.1Ã','1.2Ã','1.5Ã','1.75Ã'];
const syn=window.speechSynthesis;
function gv(){{
  const v=syn.getVoices();
  const preferred=['Samantha','Karen','Daniel','Google US English','Google UK English Female','Microsoft Zira','Microsoft Mark','Moira','Tessa'];
  for(const name of preferred){{const f=v.find(x=>x.name.includes(name)&&x.lang.startsWith('en'));if(f)return f;}}
  return v.find(x=>x.lang==='en-US')||v.find(x=>x.lang.startsWith('en'))||v[0];
}}
function fmt(s){{if(!s||isNaN(s))return'0:00';return Math.floor(s/60)+':'+(Math.floor(s%60)+'').padStart(2,'0');}}
function tick(){{if(!ts)return;el=(Date.now()-ts)/1000;document.getElementById('pf').style.width=Math.min(el/dur*100,100)+'%';document.getElementById('tc').textContent=fmt(el);}}
function setActive(i){{cards.forEach(c=>c.classList.remove('active'));if(i>=0){{cards[i].classList.add('active');cards[i].scrollIntoView({{behavior:'smooth',block:'center'}});}}}}
function playStory(i,auto=false){{
  if(!auto)allMode=false;
  syn.cancel();clearInterval(tid);going=false;paused=false;
  cur=i;const s=stories[i];
  document.getElementById('nt').textContent=s.title;
  setActive(i);
  utt=new SpeechSynthesisUtterance(s.speech);
  utt.rate=spds[si];utt.pitch=1.05;utt.volume=1;
  const v=gv();if(v)utt.voice=v;
  dur=s.speech.split(' ').length/(150*spds[si])*60;
  document.getElementById('tt').textContent=fmt(dur);
  document.getElementById('tc').textContent='0:00';
  document.getElementById('pf').style.width='0%';
  utt.onstart=()=>{{going=true;ts=Date.now();tid=setInterval(tick,600);document.getElementById('pi').innerHTML='<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>';}};
  utt.onend=()=>{{going=false;clearInterval(tid);document.getElementById('pf').style.width='100%';document.getElementById('pi').innerHTML='<path d="M8 5v14l11-7z"/>';if(allMode&&cur<stories.length-1)setTimeout(()=>playStory(cur+1,true),700);else if(allMode){{document.getElementById('nt').textContent='â All stories complete!';setActive(-1);allMode=false;}}}};
  utt.onerror=()=>{{going=false;clearInterval(tid);document.getElementById('pi').innerHTML='<path d="M8 5v14l11-7z"/>'}};
  syn.speak(utt);
}}
function togglePlay(){{
  if(!going&&!paused){{if(cur===-1&&stories.length){{allMode=true;playStory(0,true);}}else if(cur>=0)playStory(cur,allMode);return;}}
  if(going){{syn.pause();going=false;paused=true;clearInterval(tid);document.getElementById('pi').innerHTML='<path d="M8 5v14l11-7z"/>';}}
  else{{syn.resume();going=true;paused=false;ts=Date.now()-(el*1000);tid=setInterval(tick,600);document.getElementById('pi').innerHTML='<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>';}}
}}
function skipNext(){{if(cur<stories.length-1)playStory(cur+1,allMode);}}
function startAll(){{allMode=true;playStory(0,true);}}
function cycleSpd(){{si=(si+1)%spds.length;document.getElementById('sb').textContent=slab[si];if(going||paused){{const i=cur;syn.cancel();clearInterval(tid);going=false;paused=false;playStory(i,allMode);}}}}
function seek(e){{el=(e.offsetX/e.currentTarget.offsetWidth)*dur;if(cur>=0){{const i=cur;syn.cancel();clearInterval(tid);going=false;paused=false;playStory(i,allMode);}}}}
if(syn.onvoiceschanged!==undefined)syn.onvoiceschanged=()=>{{}};
</script>
</body>
</html>'''


# ââ PUSH TO GITHUB ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def push_to_github(html, date_str):
    url     = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/index.html"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r   = requests.get(url, headers=headers)
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {"message": f"Daily Brief: {date_str}", "content": base64.b64encode(html.encode()).decode()}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=headers, json=payload)
    r.raise_for_status()
    print(f"â Pushed to GitHub ({r.status_code})")



# ââ GENERATE AUDIO ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def generate_audio(sections_data, date_str, brief_label=None):
    """Generate a single MP3 of the full brief using OpenAI TTS, push to GitHub."""
    print("Generating audio via OpenAI TTS...")
    import io

    # Build full script â weather intro then stories flow without section announcements
    script_parts = []
    weather_intro = fetch_nyc_weather()
    script_parts.append(weather_intro)

    for section in SECTION_ORDER:
        stories = sections_data.get(section, [])
        if not stories:
            continue
        for story in stories:
            script_parts.append(f"{story['title']}. {story['body']}")

    script_parts.append("That's a wrap on today's brief.")
    full_script = " ".join(script_parts)

    # Split into chunks of ~4000 chars (OpenAI TTS limit is 4096)
    chunks = []
    words = full_script.split()
    current = []
    current_len = 0
    for word in words:
        if current_len + len(word) + 1 > 3800:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += len(word) + 1
    if current:
        chunks.append(" ".join(current))

    print(f"  {len(chunks)} audio chunks to generate")

    # Call OpenAI TTS for each chunk
    audio_parts = []
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i+1}/{len(chunks)}...")
        r = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers=headers,
            json={
                "model": "tts-1",
                "input": chunk,
                "voice": "alloy",
                "speed": 1.1
            }
        )
        r.raise_for_status()
        audio_parts.append(r.content)

    # Concatenate all MP3 chunks (simple binary concat works for MP3)
    full_audio = b"".join(audio_parts)
    print(f"  â Audio generated ({len(full_audio)//1024}KB)")

    # Push MP3 to GitHub
    url     = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/brief.mp3"
    gh_headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r = requests.get(url, headers=gh_headers)
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {
        "message": f"Audio Brief: {date_str}",
        "content": base64.b64encode(full_audio).decode()
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=gh_headers, json=payload)
    r.raise_for_status()
    audio_url = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}/brief.mp3"
    print(f"  â Audio pushed to GitHub")
    return audio_url

# ââ SEND EMAIL ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def send_email(date_str, page_url, audio_url, total, est_mins, brief_label=None, is_monday=False):
    body_html = f"""
<div style="font-family:Georgia,serif;max-width:500px;margin:0 auto;padding:24px;background:#f5f0e8">
  <div style="border-bottom:3px double #1a1410;padding-bottom:16px;margin-bottom:20px;text-align:center">
    <div style="font-family:sans-serif;font-size:48px;font-weight:900;letter-spacing:4px;line-height:1">
      DAILY <span style="color:#c8390a">BRIEF</span>
    </div>
    <div style="font-size:12px;color:#7a7060;margin-top:6px;font-style:italic">{brief_label or date_str}</div>
  </div>
  <table style="width:100%;border-collapse:collapse;margin-bottom:20px;font-family:sans-serif;font-size:11px;color:#7a7060;text-align:center">
    <tr>
      <td style="padding:8px;border-right:1px solid #d8d0c0"><strong style="font-size:20px;color:#1a1410;display:block">{total}</strong>Stories</td>
      <td style="padding:8px;border-right:1px solid #d8d0c0"><strong style="font-size:20px;color:#1a1410;display:block">6</strong>Sections</td>
      <td style="padding:8px"><strong style="font-size:20px;color:#1a1410;display:block">~{est_mins}m</strong>Drive</td>
    </tr>
  </table>
  <a href="{audio_url}" style="display:block;background:#c8390a;color:white;text-align:center;padding:18px;font-size:20px;text-decoration:none;letter-spacing:3px;font-family:sans-serif;font-weight:700;margin-bottom:10px">
    â¶&nbsp; PLAY AUDIO (MP3)
  </a>
  <a href="{page_url}" style="display:block;background:#1a1410;color:white;text-align:center;padding:14px;font-size:14px;text-decoration:none;letter-spacing:2px;font-family:sans-serif;font-weight:700;margin-bottom:16px">
    OPEN WEB VERSION
  </a>
  <div style="font-size:11px;color:#c8c0b0;text-align:center;line-height:1.8;font-family:sans-serif">
    World Â· US Â· Economy Â· Stocks Â· Real Estate Â· Sports Â· Basketball<br>
    Tap Play Audio â connects to Bluetooth automatically
  </div>
</div>"""

    msg = MIMEMultipart("alternative")
    subject = f"ð° Monday Brief (Weekend + Today) â {date_str}" if is_monday else f"âï¸ Your Daily Brief is ready â {date_str}"
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = YOUR_EMAIL
    msg.attach(MIMEText(body_html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PASS)
        s.sendmail(GMAIL_USER, YOUR_EMAIL, msg.as_string())
    print(f"â Email sent to {YOUR_EMAIL}")


# ââ MAIN ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def main():
    today    = datetime.date.today()
    date_str = today.strftime("%A, %B %-d, %Y")
    is_monday = today.weekday() == 0
    page_url = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}/"

    if is_monday:
        sat = (today - datetime.timedelta(days=2)).strftime("%B %-d")
        sun = (today - datetime.timedelta(days=1)).strftime("%B %-d")
        brief_label = f"Monday Brief Â· {sat}â{sun} + Today"
        print(f"\n{'='*50}\nMonday Brief â {date_str}\n{'='*50}\n")
    else:
        brief_label = date_str
        print(f"\n{'='*50}\nDaily Brief â {date_str}\n{'='*50}\n")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    sections_data = {}

    print("Loading story history...")
    history, history_sha = load_story_history()
    recent_titles = get_recent_titles(history, is_monday=is_monday)
    print(f"  {len(recent_titles)} recent stories loaded for deduplication")

    for section in SECTION_ORDER:
        print(f"\n[{section}]")
        feed_cfg = FEEDS[section]

        if section == "World News":
            feed_cfg = FEEDS[section]
            la_h   = fetch_headlines(feed_cfg["latin_america"], count=6)
            eu_h   = fetch_headlines(feed_cfg["europe"],        count=6)
            asia_h = fetch_headlines(feed_cfg["asia"],          count=6)
            me_h   = fetch_headlines(feed_cfg["middle_east"],   count=6)
            print(f"  {len(la_h)} LatAm + {len(eu_h)} Europe + {len(asia_h)} Asia + {len(me_h)} MidEast headlines")
            raw = summarize_world_news(client, la_h, eu_h, asia_h, me_h,
                                       is_monday=is_monday, recent_titles=recent_titles)

        elif section == "Economy":
            us_h    = fetch_headlines(feed_cfg["us"],    count=8)
            world_h = fetch_headlines(feed_cfg["world"], count=5)
            print(f"  {len(us_h)} US + {len(world_h)} world headlines")
            if not us_h and not world_h:
                print("  â  No headlines, skipping.")
                continue
            raw = summarize_economy(client, us_h, world_h,
                                    is_monday=is_monday, recent_titles=recent_titles)
        elif section == "Sports":
            headlines = fetch_sports_headlines()
            print(f"  {len(headlines)} total sports headlines")
            if not headlines:
                print("  â  No headlines, skipping.")
                continue
            raw = summarize_standard(client, section, headlines,
                                     is_monday=is_monday, recent_titles=recent_titles)
        else:
            headlines = fetch_headlines(feed_cfg, count=8)
            print(f"  {len(headlines)} headlines")
            if not headlines:
                print("  â  No headlines, skipping.")
                continue
            raw = summarize_standard(client, section, headlines,
                                     is_monday=is_monday, recent_titles=recent_titles)

        stories = parse_stories(raw, section)
        print(f"  â {len(stories)} stories written")
        sections_data[section] = stories

    # ââ CROSS-SECTION BODY DEDUP ââââââââââââââââââââââââââââââââââââââââââââââ
    def body_tokens(text):
        stopwords = {"the","a","an","in","on","at","of","to","and","or","but",
                     "is","are","was","were","it","its","this","that","for",
                     "with","as","by","from","has","have","had","he","she",
                     "they","their","be","been","will","would","said","also"}
        words = re.findall(r"[a-z]+", text.lower())
        return set(w for w in words if w not in stopwords and len(w) > 3)

    def extract_topic_keys(text):
        """Extract high-signal topic phrases for stricter dedup."""
        text_lower = text.lower()
        topic_patterns = [
            r"gas prices?", r"oil prices?", r"crude oil", r"gasoline",
            r"mortgage rates?", r"interest rates?", r"fed\\b", r"federal reserve",
            r"inflation", r"tariffs?", r"trade war",
        ]
        keys = set()
        for pat in topic_patterns:
            matches = re.findall(pat, text_lower)
            keys.update(m.strip() for m in matches)
        words = re.findall(r"[a-z]+", text_lower)
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i+1]}"
            if len(words[i]) > 3 and len(words[i+1]) > 3:
                keys.add(bigram)
        return keys

    all_stories_flat = []
    for section in SECTION_ORDER:
        for idx, story in enumerate(sections_data.get(section, [])):
            full_text = story["title"] + " " + story["body"]
            tokens = body_tokens(full_text)
            topics = extract_topic_keys(full_text)
            all_stories_flat.append((section, idx, tokens, topics))

    to_drop = {}
    for i in range(len(all_stories_flat)):
        sec_i, idx_i, tok_i, top_i = all_stories_flat[i]
        if not tok_i:
            continue
        for j in range(i + 1, len(all_stories_flat)):
            sec_j, idx_j, tok_j, top_j = all_stories_flat[j]
            if not tok_j:
                continue
            overlap = len(tok_i & tok_j) / min(len(tok_i), len(tok_j))
            topic_overlap = len(top_i & top_j) / max(min(len(top_i), len(top_j)), 1) if (top_i and top_j) else 0
            if overlap >= 0.30 or topic_overlap >= 0.40:
                print(f"  â  Overlap ({overlap:.0%}) â dropping [{sec_j}] story {idx_j+1} "
                      f"(similar to [{sec_i}] story {idx_i+1}, token={overlap:.0%} topic={topic_overlap:.0%})")
                to_drop.setdefault(sec_j, set()).add(idx_j)

    for section, drop_indices in to_drop.items():
        sections_data[section] = [
            s for i, s in enumerate(sections_data[section])
            if i not in drop_indices
        ]

    total    = sum(len(v) for v in sections_data.values())
    est_mins = round(total * 2.5 / 1.1)
    print(f"\nTotal: {total} stories (~{est_mins} min)")

    print("\nSaving story history...")
    history = update_history(history, sections_data)
    save_story_history(history, sha=history_sha)
    print("  â Saved")

    print("Building HTML...")
    html = build_html(sections_data, date_str, brief_label=brief_label)

    print("Pushing to GitHub Pages...")
    push_to_github(html, date_str)

    print("Generating audio...")
    audio_url = generate_audio(sections_data, date_str, brief_label=brief_label)

    print("Sending email...")
    send_email(date_str, page_url, audio_url, total, est_mins,
               brief_label=brief_label, is_monday=is_monday)

    print(f"\nâ Done! {page_url}\n")


if __name__ == "__main__":
    main()
