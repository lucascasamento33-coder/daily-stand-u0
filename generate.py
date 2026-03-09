#!/usr/bin/env python3
"""
Daily Brief Generator
Runs at 7:30am EST via Render cron job.
6 sections, 18 stories, ~36 min listen.
"""

import os, re, base64, smtplib, datetime, feedparser, requests, json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from anthropic import Anthropic

# ── ENV VARS (set in Render dashboard) ───────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN      = os.environ["GITHUB_TOKEN"]
GITHUB_USER       = os.environ["GITHUB_USER"]
GITHUB_REPO       = os.environ["GITHUB_REPO"]
GMAIL_USER        = os.environ["GMAIL_USER"]
GMAIL_APP_PASS    = os.environ["GMAIL_APP_PASS"]
YOUR_EMAIL        = os.environ["YOUR_EMAIL"]

# ── SECTION ORDER ─────────────────────────────────────────────────────────────
SECTION_ORDER = [
    "World News",
    "US News",
    "Economy",
    "US Real Estate (NYC Focus)",
    "Sports",
    "Basketball (NBA & College)",
]

# ── RSS FEEDS ─────────────────────────────────────────────────────────────────
# Economy is a dict with "us" and "world" keys (fetched separately)
# All others are plain lists of feed URLs

FEEDS = {
    "World News": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.reuters.com/reuters/worldNews",
    ],
    "US News": [
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
    ],
    "Economy": {
        "us": [
            "https://feeds.reuters.com/reuters/businessNews",
            "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
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
        "https://www.6sqft.com/feed/",
        "https://feeds.feedburner.com/StreetsOfNewYork",
        "https://www.housingwire.com/feed/",
    ],
    "Sports": [
        "https://www.espn.com/espn/rss/soccer/news",
        "https://www.espn.com/espn/rss/nfl/news",
        "https://www.espn.com/espn/rss/mlb/news",
        "https://sports.yahoo.com/soccer/rss/",
        "https://sports.yahoo.com/nfl/rss/",
    ],
    "Basketball (NBA & College)": [
        "https://www.espn.com/espn/rss/nba/news",
        "https://www.espn.com/espn/rss/ncb/news",
    ],
}

# ── DISPLAY CONFIG ────────────────────────────────────────────────────────────
SECTION_CONFIG = {
    "World News":                 {"color": "#c8390a", "tag": "WORLD"},
    "US News":                    {"color": "#1a5c8a", "tag": "US"},
    "Economy":                    {"color": "#1a6a5a", "tag": "ECONOMY"},
    "US Real Estate (NYC Focus)": {"color": "#2a7a3a", "tag": "RE · NYC"},
    "Sports":                     {"color": "#8a4a1a", "tag": "SPORTS"},
    "Basketball (NBA & College)": {"color": "#6a2a8a", "tag": "BBALL"},
}


# ── FETCH ─────────────────────────────────────────────────────────────────────

def fetch_headlines(urls, count=6, seen_titles=None):
    """Fetch headlines deduplicating across all feeds. Pass seen_titles to share across calls."""
    if seen_titles is None:
        seen_titles = set()
    items = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title   = entry.get("title", "").strip()
                summary = re.sub(r"<[^>]+>", "",
                    entry.get("summary", entry.get("description", ""))).strip()[:400]
                title_key = re.sub(r"[^a-z0-9]", "", title.lower())[:60]
                if title and len(title) > 10 and title_key not in seen_titles:
                    seen_titles.add(title_key)
                    items.append(f"- {title}. {summary}")
                if len(items) >= count:
                    break
        except Exception as e:
            print(f"  Feed error {url}: {e}")
        if len(items) >= count:
            break
    return items[:count]


def fetch_sports_headlines():
    """Fetch 4 headlines each from soccer, NFL, and MLB so Claude can pick the most impactful."""
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


# ── SUMMARIZE ─────────────────────────────────────────────────────────────────

def summarize_standard(client, section, headlines, is_monday=False, recent_titles=None):
    if recent_titles is None: recent_titles = set()
    """3 stories from a flat list of headlines."""
    re_note = ""
    if section == "US Real Estate (NYC Focus)":
        re_note = """
IMPORTANT — AUDIENCE AND FOCUS FOR THIS SECTION:
You are writing for a NYC residential real estate OWNER, not an agent or developer.
COVER: mortgage rate trends and forecasts, rent growth and rental market conditions, neighborhood trends (up-and-coming areas, quality of life changes), property tax or policy changes affecting owners, housing supply/inventory trends, economic factors affecting home values.
DO NOT COVER: specific individual home sale prices, broker commissions, agent tips, new luxury condo launches, or commercial real estate deals.
The listener owns property in NYC and wants to understand how the market is moving and what it means for their investment and living situation.
"""

    sports_note = ""
    if section == "Sports":
        sports_note = """
IMPORTANT RULES FOR SPORTS SECTION:
- Cover soccer, football (NFL/college), and baseball ONLY. No basketball — it has its own section.
- You have headlines from all three sports. Pick the 3 most NEWSWORTHY and HIGH-IMPACT stories overall.
- Prioritize: blockbuster trades, major signings, championship results, serious injuries to star players, historic achievements, big upsets.
- Deprioritize: routine game recaps, minor roster moves, preview fluff.
- It's fine to have 2 stories from one sport if they're both genuinely big news.
- If something is a genuine blockbuster (e.g. a massive trade or a historic result), lead with it regardless of sport.
"""

    monday_note = ""
    if is_monday:
        monday_note = """
IMPORTANT — TODAY IS MONDAY:
This brief covers THREE days of news: Saturday, Sunday, and Monday morning.
- Include the most important stories from the weekend (Saturday + Sunday) AND any breaking Monday morning news.
- Do not limit yourself to just the weekend — if there is significant Monday morning news, include it.
- Across the 3 stories for this section, aim to balance weekend recap with any fresh Monday news where relevant.
- Label weekend stories naturally in the text (e.g. "Over the weekend..." or "On Sunday...") and Monday news as current.
"""

    recent_note = ""
    if recent_titles:
        recent_note = f"""
AVOID DUPLICATE TOPICS: The following story topics have already been covered in recent days. Do NOT write about these topics again unless there is a major NEW development that significantly changes the story:
{chr(10).join(f"- {t}" for t in list(recent_titles)[:30])}
"""

    prompt = f"""You are a professional radio news writer for a morning commute audio brief.

Section: {section}{re_note}{sports_note}{monday_note}{recent_note}
Today's headlines:
{chr(10).join(headlines)}

Write exactly 3 news stories. Format each one exactly like this:

###
TITLE: Your story title here
Your 3-5 sentence story body here. Write conversationally for audio — clear, confident, like a radio anchor. No filler like "In today's news..."
###

Output only the 3 stories. No extra text."""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


def summarize_economy(client, us_headlines, world_headlines, is_monday=False, recent_titles=None):
    if recent_titles is None: recent_titles = set()
    """2 US economy stories + 1 world economy story."""
    monday_note = ""
    if is_monday:
        monday_note = """
IMPORTANT — TODAY IS MONDAY:
Cover the most important economic stories from Saturday, Sunday, AND Monday morning.
Include weekend market moves, policy announcements, or economic news that broke over the weekend, plus any fresh Monday morning economic news.
Label weekend stories naturally (e.g. "Over the weekend...") and Monday news as current.
"""

    recent_note_econ = ""
    if recent_titles:
        recent_note_econ = f"""
AVOID DUPLICATE TOPICS: The following story topics have already been covered in recent days. Do NOT repeat them unless there is a major new development:
{chr(10).join(f"- {t}" for t in list(recent_titles)[:20])}
"""

    prompt = f"""You are a professional radio news writer for a morning commute audio brief.

Section: Economy{monday_note}{recent_note_econ}

US Economy headlines:
{chr(10).join(us_headlines)}

World Economy headlines:
{chr(10).join(world_headlines)}

Write exactly 3 stories:
- Story 1: US economy
- Story 2: US economy
- Story 3: World economy

Format each one exactly like this:

###
TITLE: Your story title here
Your 3-5 sentence story body here. Conversational, for audio listening. Include market data, numbers, or policy details where available. Clear and confident — no filler phrases.
###

Output only the 3 stories. No extra text."""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text.strip()


def parse_stories(raw, section):
    stories = []
    blocks = re.findall(r"###\s*(.*?)\s*###", raw, re.DOTALL)
    for block in blocks:
        block = block.strip()
        title_match = re.search(r"TITLE:\s*(.+)", block)
        title = title_match.group(1).strip() if title_match else f"{section} Update"
        body  = re.sub(r"TITLE:.+\n?", "", block).strip()
        if body:
            stories.append({"title": title, "body": body})
    return stories[:3]


# ── BUILD HTML ────────────────────────────────────────────────────────────────

def esc(s):
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
             .replace('"',"&quot;").replace("'","&#39;"))

def build_html(sections_data, date_str, brief_label=None):
    total    = sum(len(v) for v in sections_data.values())
    est_mins = total * 2

    cards_html = ""
    idx = 0
    for section in SECTION_ORDER:
        stories = sections_data.get(section, [])
        if not stories:
            continue
        cfg = SECTION_CONFIG[section]
        cards_html += f'<div class="sh"><span class="sl" style="background:{cfg["color"]}">{cfg["tag"]}</span><div class="sln"></div></div><div class="sl-list">'
        for story in stories:
            speech = esc(f"{section}. {story['title']}. {story['body']}")
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
<title>Daily Brief · {date_str}</title>
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
    <span class="badge">● AI GENERATED</span>
  </div>
  <div class="logo">Daily <span>Brief</span></div>
  <div class="hrule"></div>
  <div class="tagline">{brief_label or date_str} · {total} stories · ~{est_mins} minutes</div>
</div>
<div class="stats">
  <div class="stat"><span class="sv">{total}</span>Stories</div>
  <div class="stat"><span class="sv">6</span>Sections</div>
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
  Generated by Claude AI · BBC · Reuters · FT · MarketWatch · NYT · ESPN · The Real Deal<br>
  {date_str} · Your Daily Brief
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
    <button class="spd" id="sb" onclick="cycleSpd()">1×</button>
  </div>
</div>
<script>
const cards=Array.from(document.querySelectorAll('.card'));
const stories=cards.map(c=>({{title:c.querySelector('.stitle').textContent,speech:c.dataset.speech}}));
let cur=-1,going=false,paused=false,allMode=false,si=1,utt=null,dur=0,el=0,ts=null,tid=null;
const spds=[0.85,1,1.2,1.5,1.75],slab=['0.85×','1×','1.2×','1.5×','1.75×'];
const syn=window.speechSynthesis;
function gv(){{const v=syn.getVoices();return v.find(x=>x.lang==='en-US'&&(x.name.includes('Samantha')||x.name.includes('Google')))||v.find(x=>x.lang.startsWith('en'))||v[0];}}
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
  utt.rate=spds[si];utt.pitch=1;utt.volume=1;
  const v=gv();if(v)utt.voice=v;
  dur=s.speech.split(' ').length/(150*spds[si])*60;
  document.getElementById('tt').textContent=fmt(dur);
  document.getElementById('tc').textContent='0:00';
  document.getElementById('pf').style.width='0%';
  utt.onstart=()=>{{going=true;ts=Date.now();tid=setInterval(tick,600);document.getElementById('pi').innerHTML='<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>';}};
  utt.onend=()=>{{going=false;clearInterval(tid);document.getElementById('pf').style.width='100%';document.getElementById('pi').innerHTML='<path d="M8 5v14l11-7z"/>';if(allMode&&cur<stories.length-1)setTimeout(()=>playStory(cur+1,true),700);else if(allMode){{document.getElementById('nt').textContent='✓ All stories complete!';setActive(-1);allMode=false;}}}};
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



# ── CROSS-DAY DEDUPLICATION ───────────────────────────────────────────────────

HISTORY_FILE = "story_history.json"
MAX_HISTORY_DAYS = 5  # keep titles for this many days to prevent repeats

def load_story_history():
    """Load previously used story titles from GitHub."""
    url     = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{HISTORY_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        data = r.json()
        raw  = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(raw), data.get("sha")
    return {}, None


def save_story_history(history, sha=None):
    """Save used story titles back to GitHub."""
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
    """Return a set of normalized titles used in recent days."""
    today = datetime.date.today()
    cutoff_days = MAX_HISTORY_DAYS + (2 if is_monday else 0)  # on Monday, also exclude weekend
    seen = set()
    for date_key, titles in history.items():
        try:
            d = datetime.date.fromisoformat(date_key)
            if (today - d).days <= cutoff_days:
                seen.update(titles)
        except Exception:
            pass
    return seen


def normalize_title(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())[:80]


def update_history(history, stories_by_section):
    """Add today's story titles to history, pruning old entries."""
    today_key = datetime.date.today().isoformat()
    today_titles = []
    for stories in stories_by_section.values():
        for story in stories:
            today_titles.append(normalize_title(story["title"]))
    history[today_key] = today_titles
    # Prune entries older than MAX_HISTORY_DAYS + 3
    cutoff = datetime.date.today() - datetime.timedelta(days=MAX_HISTORY_DAYS + 3)
    history = {k: v for k, v in history.items()
               if datetime.date.fromisoformat(k) >= cutoff}
    return history


# ── PUSH TO GITHUB ────────────────────────────────────────────────────────────

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
    print(f"✓ Pushed to GitHub ({r.status_code})")


# ── SEND EMAIL ────────────────────────────────────────────────────────────────

def send_email(date_str, page_url, total, est_mins, brief_label=None, is_monday=False):
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
  <a href="{page_url}" style="display:block;background:#1a1410;color:white;text-align:center;padding:18px;font-size:20px;text-decoration:none;letter-spacing:3px;font-family:sans-serif;font-weight:700;margin-bottom:16px">
    ▶&nbsp; OPEN &amp; PLAY
  </a>
  <div style="font-size:11px;color:#c8c0b0;text-align:center;line-height:1.8;font-family:sans-serif">
    World · US · Economy · Real Estate · Sports · Basketball<br>
    Connect Bluetooth · Tap Play All · Drive
  </div>
</div>"""

    msg = MIMEMultipart("alternative")
    subject = f"📰 Monday Brief (Weekend + Today) — {date_str}" if is_monday else f"☀️ Your Daily Brief is ready — {date_str}"
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = YOUR_EMAIL
    msg.attach(MIMEText(body_html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_APP_PASS)
        s.sendmail(GMAIL_USER, YOUR_EMAIL, msg.as_string())
    print(f"✓ Email sent to {YOUR_EMAIL}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    today    = datetime.date.today()
    date_str = today.strftime("%A, %B %-d, %Y")
    is_monday = today.weekday() == 0  # Monday = 0
    page_url = f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}/"
    if is_monday:
        sat = (today - datetime.timedelta(days=2)).strftime("%B %-d")
        sun = (today - datetime.timedelta(days=1)).strftime("%B %-d")
        brief_label = f"Monday Brief · Weekend + Today · {sat}–{sun} & {today.strftime('%B %-d')}"
        print(f"\n{'='*50}\nMonday — Weekend Catch-Up Brief — {date_str}\n{'='*50}\n")
    else:
        brief_label = date_str
        print(f"\n{'='*50}\nDaily Brief Generator — {date_str}\n{'='*50}\n")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    sections_data = {}

    print("Loading story history for deduplication...")
    history, history_sha = load_story_history()
    recent_titles = get_recent_titles(history, is_monday=is_monday)
    print(f"  {len(recent_titles)} recent topic keys loaded")

    for section in SECTION_ORDER:
        feed_cfg = FEEDS[section]
        print(f"[{section}]")

        if section == "Economy":
            us_h    = fetch_headlines(feed_cfg["us"],    count=6)
            world_h = fetch_headlines(feed_cfg["world"], count=4)
            print(f"  {len(us_h)} US + {len(world_h)} world headlines → Claude writing 2 US + 1 world...")
            if not us_h and not world_h:
                print("  ⚠ No headlines, skipping.")
                continue
            raw = summarize_economy(client, us_h, world_h, is_monday=is_monday, recent_titles=recent_titles)
        elif section == "Sports":
            headlines = fetch_sports_headlines()
            print(f"  {len(headlines)} mixed sports headlines → Claude writing 3 stories...")
            if not headlines:
                print("  ⚠ No headlines, skipping.")
                continue
            raw = summarize_standard(client, section, headlines)
        else:
            headlines = fetch_headlines(feed_cfg, count=6)
            print(f"  {len(headlines)} headlines → Claude writing 3 stories...")
            if not headlines:
                print("  ⚠ No headlines, skipping.")
                continue
            raw = summarize_standard(client, section, headlines, is_monday=is_monday, recent_titles=recent_titles)

        stories = parse_stories(raw, section)
        print(f"  ✓ {len(stories)} stories done")
        sections_data[section] = stories

    total    = sum(len(v) for v in sections_data.values())
    est_mins = total * 2
    print(f"\nTotal: {total} stories (~{est_mins} min)")

    print("Saving story history...")
    history = update_history(history, sections_data)
    save_story_history(history, sha=history_sha)
    print("  ✓ Story history saved")

    print("Building HTML...")
    html = build_html(sections_data, date_str, brief_label=brief_label)

    print("Pushing to GitHub Pages...")
    push_to_github(html, date_str)

    print("Sending Gmail...")
    send_email(date_str, page_url, total, est_mins, brief_label=brief_label, is_monday=is_monday)

    print(f"\n✅ Done! {page_url}\n")


if __name__ == "__main__":
    main()
