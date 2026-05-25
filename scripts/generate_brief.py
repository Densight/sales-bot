"""
Sales Intelligence Bot — v4
Densight Labs · Applied AI Live, Episode 1

FLOW:
  1. Load config.yml — company context, keywords, settings
  2. Fetch signals (HackerNews + RSS + GitHub) — free
  3. ONE Haiku call — identifies companies as JSON — ~$0.003
  4. Hunter.io — enriches each company (emails + contacts) — free tier
  5. Post full brief to GitHub Issue
"""

import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, date

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# ── CONFIG ────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN      = os.environ["GITHUB_TOKEN"]
GITHUB_REPO       = os.environ["GITHUB_REPO"]
HUNTER_API_KEY    = os.environ["HUNTER_API_KEY"]

def load_config() -> dict:
    """Load config.yml if available, fall back to env vars."""
    cfg = {}
    if HAS_YAML:
        try:
            with open("config.yml") as f:
                cfg = yaml.safe_load(f) or {}
            print("✅ Loaded config.yml")
        except FileNotFoundError:
            print("⚠️  config.yml not found — using environment variables")

    company = cfg.get("company", {})
    signals = cfg.get("signals", {})
    output  = cfg.get("output", {})

    return {
        "company_name":    company.get("name")    or os.environ.get("YOUR_COMPANY",  "Densight Labs"),
        "service":         company.get("service") or os.environ.get("YOUR_SERVICE",  "AI implementation and workflow automation"),
        "target_market":   company.get("target_market") or os.environ.get("TARGET_MARKET", "US-based mid-market companies scaling operations"),
        "keywords":        signals.get("keywords") or ["hiring", "launched", "show hn", "raised", "series a", "series b", "funding"],
        "num_companies":   output.get("companies") or 3,
    }

# ── STEP 1: FETCH SIGNALS ─────────────────────────────────────────────────────

def fetch_hackernews(keywords: list):
    print("📡 HackerNews...")
    try:
        top = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10).json()
        signals = []
        for sid in top[:100]:
            s = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5).json()
            if not s: continue
            t = s.get("title", "").lower()
            if any(k in t for k in keywords):
                signals.append(s.get("title", ""))
            if len(signals) >= 8: break
        print(f"   → {len(signals)}")
        return signals
    except Exception as e:
        print(f"   ⚠️ {e}")
        return []

def fetch_rss():
    print("📡 RSS...")
    feeds = [
        ("TechCrunch", "https://techcrunch.com/category/startups/feed/"),
        ("VentureBeat", "https://venturebeat.com/category/ai/feed/"),
    ]
    signals = []
    for name, url in feeds:
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:5]:
                t = item.findtext("title", "").strip()
                if t: signals.append(t)
        except: pass
    print(f"   → {len(signals)}")
    return signals

def fetch_github():
    print("📡 GitHub...")
    try:
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": "created:>2026-05-01 stars:>50", "sort": "stars", "order": "desc", "per_page": 8},
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        signals = []
        for r in resp.json().get("items", []):
            desc = (r.get("description") or "")[:60]
            signals.append(f"{r.get('full_name','')} — {desc} ({r.get('stargazers_count',0)} stars)")
        print(f"   → {len(signals)}")
        return signals
    except Exception as e:
        print(f"   ⚠️ {e}")
        return []

# ── STEP 2: CLAUDE — IDENTIFY COMPANIES ──────────────────────────────────────

def identify_companies(hn, rss, github, cfg) -> list:
    print(f"\n🤖 Claude (Haiku) — identifying {cfg['num_companies']} companies...")

    signals_text = "\n".join(
        [f"HN: {s}"     for s in hn]     +
        [f"NEWS: {s}"   for s in rss]    +
        [f"GITHUB: {s}" for s in github]
    )

    prompt = f"""You are a sales analyst for {cfg['company_name']}.
Service: {cfg['service']}
Target: {cfg['target_market']}

From these signals pick the {cfg['num_companies']} best US outreach opportunities.
Return ONLY a JSON array, no markdown, no preamble.

[
  {{
    "company_name": "Acme",
    "domain": "acme.com",
    "industry": "Fintech",
    "signal": "Raised $20M Series A",
    "why_now": "Scaling fast = automation pain incoming",
    "pain_points": ["Pain 1", "Pain 2", "Pain 3"],
    "outreach_angle": "One sentence pitch specific to {cfg['company_name']}",
    "first_line": "Cold email opener",
    "talking_points": ["Point 1", "Point 2"],
    "watch_out": "One red flag",
    "confidence": "High"
  }}
]

Only US companies. Be ruthlessly selective. Make everything specific to this company's situation.

SIGNALS:
{signals_text}"""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1500, "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )

    result = resp.json()
    usage  = result.get("usage", {})
    cost   = (usage.get("input_tokens", 0) * 0.00000025) + (usage.get("output_tokens", 0) * 0.00000125)
    print(f"   → {usage.get('input_tokens',0)} in / {usage.get('output_tokens',0)} out | Cost: ${cost:.4f}")

    raw = result["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]

    companies = json.loads(raw.strip())
    print(f"   → Identified: {[c['company_name'] for c in companies]}")
    return companies

# ── STEP 3: HUNTER.IO — ENRICH EACH COMPANY ──────────────────────────────────

def enrich_with_hunter(domain: str) -> dict:
    try:
        resp = requests.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": HUNTER_API_KEY, "limit": 5},
            timeout=10,
        )
        data = resp.json().get("data", {})
        if not data: return {}

        result = {
            "website":     data.get("domain", domain),
            "company":     data.get("organization", ""),
            "description": data.get("description", ""),
            "linkedin":    data.get("linkedin", ""),
            "twitter":     data.get("twitter", ""),
            "contacts":    []
        }

        priority = ["ceo", "cto", "founder", "vp", "head", "director", "chief", "president", "co-founder"]
        emails   = data.get("emails", [])
        emails.sort(key=lambda e: 0 if any(p in (e.get("position") or "").lower() for p in priority) else 1)

        for e in emails[:4]:
            result["contacts"].append({
                "name":       f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                "title":      e.get("position", ""),
                "email":      e.get("value", ""),
                "linkedin":   e.get("linkedin", ""),
                "confidence": e.get("confidence", 0),
            })

        return result
    except Exception as e:
        print(f"      ⚠️ Hunter failed for {domain}: {e}")
        return {}

# ── STEP 4: BUILD ISSUE BODY ──────────────────────────────────────────────────

def build_issue_body(companies: list, total_signals: int, cfg: dict) -> str:
    sections = []

    for i, c in enumerate(companies, 1):
        hunter = enrich_with_hunter(c.get("domain", ""))
        print(f"   [{i}/{cfg['num_companies']}] {c['company_name']} → {len(hunter.get('contacts', []))} contacts found")

        pain  = "\n".join(f"- {p}" for p in c.get("pain_points", []))
        talks = "\n".join(f"- {t}" for t in c.get("talking_points", []))

        # Company links from Hunter
        links = []
        if hunter.get("website"):  links.append(f"🌐 [{hunter['website']}](https://{hunter['website']})")
        if hunter.get("linkedin"): links.append(f"💼 [LinkedIn](https://linkedin.com/company/{hunter['linkedin']})")
        if hunter.get("twitter"):  links.append(f"🐦 [@{hunter['twitter']}](https://twitter.com/{hunter['twitter']})")
        company_links = "  ".join(links)
        if hunter.get("description"):
            company_links += f"\n_{hunter['description']}_"

        # Contacts table
        if hunter.get("contacts"):
            rows = "\n".join(
                f"| {ct.get('name','N/A')} | {ct.get('title','N/A')} | {ct.get('email','N/A')} | {'[LinkedIn]('+ct['linkedin']+')' if ct.get('linkedin') else 'N/A'} | {ct.get('confidence',0)}% |"
                for ct in hunter["contacts"]
            )
            contacts_md = f"""
### 👤 Decision Makers
| Name | Title | Email | LinkedIn | Confidence |
|------|-------|-------|----------|------------|
{rows}"""
        else:
            contacts_md = "\n### 👤 Decision Makers\n_No contacts found on Hunter.io for this domain_"

        sections.append(f"""---
### {i}. {c['company_name']} — {c.get('industry','')} &nbsp;`{c.get('confidence','')}`

{company_links}

**📡 Signal:** {c['signal']}

**⏰ Why now:** {c['why_now']}

**😤 Pain points:**
{pain}

**🎯 Outreach angle:** {c['outreach_angle']}

**✉️ First line:**
> _{c['first_line']}_

**📞 Talking points:**
{talks}

**⚠️ Watch out:** {c.get('watch_out','N/A')}
{contacts_md}
""")

    header = f"""## 🎯 Sales Intelligence Brief — {cfg['company_name']}
**Date:** {datetime.now().strftime("%d %b %Y, %H:%M")} | **Signals:** {total_signals} | **Powered by:** GitHub Actions + Claude API + Hunter.io

"""
    footer = "\n---\n*Auto-generated by Densight Labs Sales Intelligence Bot · Edit `config.yml` to personalise*"
    return header + "\n".join(sections) + footer

# ── STEP 5: POST TO GITHUB ISSUE ─────────────────────────────────────────────

def create_issue(body: str):
    print("\n📌 Creating GitHub Issue...")
    today = date.today().strftime("%d %b %Y")
    owner, repo = GITHUB_REPO.split("/")
    resp = requests.post(
        f"https://api.github.com/repos/{owner}/{repo}/issues",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
        json={"title": f"🎯 Sales Intelligence Brief — {today}", "body": body, "labels": ["sales-intel", "automated"]},
        timeout=15,
    )
    if resp.status_code == 201:
        print(f"   → {resp.json()['html_url']}")
    else:
        print(f"   ⚠️ {resp.status_code}: {resp.text[:200]}")

# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  SALES INTELLIGENCE BOT v4 — Densight Labs")
    print(f"  {datetime.now().strftime('%d %b %Y, %H:%M PKT')}")
    print("=" * 50)

    cfg = load_config()
    print(f"  Company: {cfg['company_name']}")
    print(f"  Target:  {cfg['target_market']}")

    hn     = fetch_hackernews(cfg["keywords"])
    rss    = fetch_rss()
    github = fetch_github()
    total  = len(hn) + len(rss) + len(github)
    print(f"\n✅ {total} signals collected")

    if total == 0:
        print("⚠️  No signals. Exiting.")
        return

    companies = identify_companies(hn, rss, github, cfg)

    print("\n🔍 Enriching with Hunter.io...")
    body = build_issue_body(companies, total, cfg)
    create_issue(body)

    print("\n✅ DONE")

if __name__ == "__main__":
    main()
