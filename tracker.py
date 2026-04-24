"""
Runs ONCE per GitHub Actions invocation. Uses the YouTube Data API
to fetch the current view, like, and comment counts for VIDEO_URL
and appends a row to view_counts.csv. Also regenerates index.html.
"""

import csv
import json as _json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import requests

# -------- CONFIG --------
VIDEO_URL = "https://www.youtube.com/watch?v=6IxPD-jNdwM"
CURRENT_TITLE = "Clayface (2026)"
PRODUCT_TITLE = "Trailer Watch"
PRODUCT_SUBTITLE = "Tracking the first 72 hours of trailer drop"
OUTPUT_CSV = "view_counts.csv"
OUTPUT_HTML = "index.html"
# ------------------------

API_KEY = os.environ.get("YOUTUBE_API_KEY")
CSV_FIELDS = ["timestamp_utc", "view_count", "like_count", "comment_count", "title"]


def extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname == "youtu.be":
        return parsed.path.lstrip("/")
    qs = parse_qs(parsed.query)
    if "v" in qs:
        return qs["v"][0]
    raise ValueError(f"Could not extract video id from URL: {url}")


def fetch_stats(video_url: str) -> dict:
    if not API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY environment variable is not set")
    video_id = extract_video_id(video_url)
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "statistics,snippet,contentDetails", "id": video_id, "key": API_KEY},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    items = data.get("items", [])
    if not items:
        raise RuntimeError(f"No video found for id={video_id}. API response: {data}")
    stats = items[0]["statistics"]
    snippet = items[0]["snippet"]
    return {
        "view_count": int(stats.get("viewCount", 0)),
        "like_count": int(stats.get("likeCount", 0)) if "likeCount" in stats else None,
        "comment_count": int(stats.get("commentCount", 0)) if "commentCount" in stats else None,
        "title": snippet.get("title", ""),
        "channel": snippet.get("channelTitle", ""),
        "published_at": snippet.get("publishedAt", ""),
    }


def append_row(path: str, row: dict) -> None:
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def read_rows(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def safe_int(val):
    try:
        return int(val) if val not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def compute_derived(rows: list) -> dict:
    valid = []
    for r in rows:
        v = safe_int(r.get("view_count"))
        if v is None:
            continue
        try:
            t = datetime.fromisoformat(r["timestamp_utc"])
        except (ValueError, KeyError):
            continue
        valid.append({
            "v": v,
            "l": safe_int(r.get("like_count")),
            "c": safe_int(r.get("comment_count")),
            "t": t,
        })

    stats = {
        "latest_views": None, "latest_likes": None, "latest_comments": None,
        "last_delta": None, "avg_per_hour": None, "peak_per_hour": None,
        "hours_elapsed": None, "h_plus": None, "engagement_rate": None,
    }
    if not valid:
        return stats

    latest = valid[-1]
    stats["latest_views"] = latest["v"]
    stats["latest_likes"] = latest["l"]
    stats["latest_comments"] = latest["c"]

    # Engagement rate = (likes + comments) / views * 100
    if latest["v"] and latest["l"] is not None and latest["c"] is not None and latest["v"] > 0:
        stats["engagement_rate"] = round(((latest["l"] + latest["c"]) / latest["v"]) * 100, 2)

    if len(valid) >= 1:
        first = valid[0]
        now = datetime.now(timezone.utc)
        h_plus = max((now - first["t"]).total_seconds() / 3600, 0)
        stats["h_plus"] = int(h_plus)

    if len(valid) >= 2:
        prev = valid[-2]
        elapsed_h = max((latest["t"] - prev["t"]).total_seconds() / 3600, 0.0001)
        stats["last_delta"] = round((latest["v"] - prev["v"]) / elapsed_h)
        first = valid[0]
        total_h = max((latest["t"] - first["t"]).total_seconds() / 3600, 0.0001)
        stats["avg_per_hour"] = round((latest["v"] - first["v"]) / total_h)
        stats["hours_elapsed"] = round(total_h, 1)
        peak = 0
        for i in range(1, len(valid)):
            h = max((valid[i]["t"] - valid[i-1]["t"]).total_seconds() / 3600, 0.0001)
            rate = (valid[i]["v"] - valid[i-1]["v"]) / h
            if rate > peak:
                peak = rate
        stats["peak_per_hour"] = round(peak)

    return stats


def fmt_num(n):
    return "—" if n is None else f"{n:,}"


def fmt_signed(n):
    if n is None:
        return "—"
    return f"{'+' if n >= 0 else ''}{n:,}"


def compute_next_run_minutes():
    """Next scheduled cron is :30 or :45 of each hour (primary + backup)."""
    now = datetime.now(timezone.utc)
    m = now.minute
    if m < 30:
        return 30 - m
    elif m < 45:
        return 45 - m
    else:
        return (60 - m) + 30  # wrap to next hour's :30


def build_html(rows: list, meta: dict) -> str:
    derived = compute_derived(rows)
    video_id = extract_video_id(VIDEO_URL)
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    fallback_thumb = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

    channel = meta.get("channel", "")
    published_at = meta.get("published_at", "")
    yt_title = meta.get("title", "") or (rows[-1].get("title", "") if rows else "")

    prev_v = None
    trs = []
    chart_points = []
    for i, r in enumerate(rows, start=1):
        v = safe_int(r.get("view_count"))
        l = safe_int(r.get("like_count"))
        c = safe_int(r.get("comment_count"))
        v_str = fmt_num(v)
        l_str = fmt_num(l)
        c_str = fmt_num(c)
        if v is not None and prev_v is not None:
            d = v - prev_v
            delta_str = f"{'+' if d >= 0 else ''}{d:,}"
        else:
            delta_str = "—"
        if v is not None:
            prev_v = v
            chart_points.append({
                "i": i - 1,
                "v": v,
                "l": l or 0,
                "c": c or 0,
                "t": r.get("timestamp_utc", ""),
            })

        ts = r.get("timestamp_utc", "")
        try:
            dt = datetime.fromisoformat(ts)
            date_str = dt.strftime("%b ") + str(dt.day)
            time_str = dt.strftime("%H:%M UTC")
        except Exception:
            date_str, time_str = ts, ""

        trs.append(
            f'<tr><td class="idx">{i:02d}</td>'
            f'<td><div class="tstack"><span class="tdate">{date_str}</span><span class="ttime">{time_str}</span></div></td>'
            f'<td class="num">{v_str}</td>'
            f'<td class="num delta">{delta_str}</td>'
            f'<td class="num">{l_str}</td>'
            f'<td class="num">{c_str}</td></tr>'
        )
    table_rows = "\n".join(trs) if trs else '<tr><td colspan="6" class="empty">No readings yet. First reading appears within the hour.</td></tr>'
    chart_data_json = _json.dumps(chart_points)

    latest_views_str = fmt_num(derived["latest_views"])
    latest_likes_str = fmt_num(derived["latest_likes"])
    latest_comments_str = fmt_num(derived["latest_comments"])
    last_delta_str = fmt_signed(derived["last_delta"])
    avg_str = fmt_signed(derived["avg_per_hour"])
    peak_str = fmt_num(derived["peak_per_hour"])
    engagement_rate = derived["engagement_rate"]
    engagement_str = f"{engagement_rate}%" if engagement_rate is not None else "—"

    # Engagement benchmark label
    if engagement_rate is None:
        engagement_label = "—"
    elif engagement_rate < 1.5:
        engagement_label = "below benchmark"
    elif engagement_rate < 3.0:
        engagement_label = "at benchmark"
    else:
        engagement_label = "above benchmark"

    h_plus = derived["h_plus"] if derived["h_plus"] is not None else 0
    readings_count = len(rows)
    next_reading_min = compute_next_run_minutes()

    latest_ts = rows[-1]["timestamp_utc"] if rows else ""
    first_ts = rows[0]["timestamp_utc"] if rows else ""

    # Format published date
    pub_display = ""
    if published_at:
        try:
            pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            pub_display = pub_dt.strftime("%b ") + str(pub_dt.day) + pub_dt.strftime(", %Y · %H:%M UTC")
        except Exception:
            pub_display = published_at

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{PRODUCT_TITLE} — {CURRENT_TITLE}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=Manrope:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #07090c;
    --bg-elev: #0e1319;
    --bg-card: #11171f;
    --border: #1c2430;
    --border-hover: #28344a;
    --text: #e8edf2;
    --text-dim: #8a95a5;
    --text-faint: #5a6676;
    --cyan: #22d3ee;
    --teal: #14b8a6;
    --amber: #fbbf24;
    --orange: #f97316;
    --violet: #a78bfa;
    --pink: #ec4899;
    --accent-soft: rgba(34, 211, 238, 0.08);
    --positive: #4ade80;
  }}

  * {{ box-sizing: border-box; }}

  html, body {{
    margin: 0; padding: 0;
    background: var(--bg); color: var(--text);
    font-family: 'Manrope', -apple-system, sans-serif;
    font-weight: 400;
    -webkit-font-smoothing: antialiased;
    min-height: 100vh;
  }}

  body {{
    background:
      radial-gradient(ellipse 80% 50% at 50% -10%, var(--accent-soft), transparent),
      radial-gradient(ellipse 60% 40% at 85% 100%, rgba(20,184,166,0.05), transparent),
      var(--bg);
    background-attachment: fixed;
  }}

  .container {{
    max-width: 1200px;
    margin: 0 auto;
    padding: 3rem 2rem 4rem;
  }}

  .header {{
    display: flex; justify-content: space-between; align-items: center;
    border-bottom: 1px solid var(--border);
    padding-bottom: 1.5rem; margin-bottom: 2rem;
    flex-wrap: wrap; gap: 1.5rem;
  }}
  .brand {{ display: flex; align-items: center; gap: 0.75rem; }}
  .brand-dot {{
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--cyan);
    box-shadow: 0 0 14px var(--cyan);
    animation: pulse 2s ease-in-out infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50% {{ opacity: 0.5; transform: scale(0.85); }}
  }}
  .brand-label {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem; font-weight: 500;
    letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--text-dim);
  }}
  .header-cluster {{ display: flex; align-items: center; gap: 2rem; }}
  .h-plus {{ display: flex; flex-direction: column; align-items: flex-end; line-height: 1; }}
  .h-plus-value {{
    font-family: 'Manrope', sans-serif;
    font-weight: 800; font-size: 2rem;
    letter-spacing: -0.03em; line-height: 1;
    background: linear-gradient(90deg, var(--cyan), var(--teal));
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
    font-variant-numeric: tabular-nums;
  }}
  .h-plus-label {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.6rem; letter-spacing: 0.2em; text-transform: uppercase;
    color: var(--text-faint); margin-top: 0.25rem;
  }}
  .header-meta {{ display: flex; flex-direction: column; gap: 0.15rem; align-items: flex-end; }}
  .header-meta-row {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; color: var(--text-faint);
    letter-spacing: 0.1em; text-transform: uppercase;
  }}
  .header-meta-row strong {{ color: var(--text-dim); font-weight: 500; }}

  /* Title block */
  .title-block {{ margin-bottom: 2rem; }}
  h1.product-title {{
    font-family: 'Manrope', sans-serif;
    font-size: clamp(2.25rem, 5.5vw, 3.5rem);
    font-weight: 800; margin: 0 0 0.4rem 0;
    letter-spacing: -0.04em; line-height: 1;
    background: linear-gradient(90deg, var(--cyan) 0%, var(--teal) 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .product-subtitle {{
    font-family: 'Manrope', sans-serif;
    font-size: clamp(0.95rem, 1.6vw, 1.1rem);
    color: var(--text-dim); font-weight: 400;
    margin: 0 0 1.75rem 0; letter-spacing: -0.01em;
  }}

  /* Trailer card */
  .trailer-card {{
    display: grid;
    grid-template-columns: 280px 1fr;
    gap: 1.5rem;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 2.5rem;
  }}
  @media (max-width: 720px) {{
    .trailer-card {{ grid-template-columns: 1fr; }}
  }}
  .trailer-thumb {{
    position: relative;
    aspect-ratio: 16 / 9;
    overflow: hidden;
    background: var(--bg-elev);
  }}
  .trailer-thumb img {{
    width: 100%; height: 100%; object-fit: cover;
    display: block; transition: transform 0.4s ease;
  }}
  .trailer-thumb:hover img {{ transform: scale(1.03); }}
  .trailer-thumb::after {{
    content: ''; position: absolute; inset: 0;
    background: linear-gradient(135deg, transparent 60%, rgba(0,0,0,0.4));
    pointer-events: none;
  }}
  .live-badge {{
    position: absolute; top: 0.75rem; left: 0.75rem;
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.3rem 0.6rem;
    background: rgba(7, 9, 12, 0.85);
    backdrop-filter: blur(8px);
    border: 1px solid var(--cyan);
    border-radius: 3px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--cyan);
    z-index: 2;
  }}
  .live-badge-dot {{
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--cyan);
    box-shadow: 0 0 8px var(--cyan);
    animation: pulse 1.6s ease-in-out infinite;
  }}
  .trailer-info {{
    padding: 1.5rem 1.5rem 1.5rem 0;
    display: flex; flex-direction: column;
    justify-content: space-between;
    min-width: 0;
  }}
  @media (max-width: 720px) {{
    .trailer-info {{ padding: 0 1.5rem 1.5rem; }}
  }}
  .trailer-eyebrow {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--text-faint);
    margin-bottom: 0.5rem;
  }}
  .trailer-title {{
    font-family: 'Manrope', sans-serif;
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    line-height: 1.2;
    color: var(--text);
    margin: 0 0 0.5rem 0;
  }}
  .trailer-channel {{
    font-family: 'Manrope', sans-serif;
    font-size: 0.9rem;
    color: var(--text-dim);
    margin-bottom: 1rem;
  }}
  .trailer-meta {{
    display: flex; flex-wrap: wrap; gap: 1rem;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; color: var(--text-faint);
    letter-spacing: 0.05em;
    margin-bottom: 1.25rem;
    padding-bottom: 1.25rem;
    border-bottom: 1px solid var(--border);
  }}
  .trailer-meta strong {{ color: var(--text-dim); font-weight: 500; }}
  .trailer-link {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem; font-weight: 500;
    letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--cyan);
    text-decoration: none;
    align-self: flex-start;
    padding: 0.5rem 0.9rem;
    border: 1px solid var(--cyan);
    border-radius: 4px;
    background: transparent;
    transition: all 0.15s ease;
  }}
  .trailer-link:hover {{
    background: var(--accent-soft);
    box-shadow: 0 0 16px rgba(34, 211, 238, 0.25);
  }}

  /* Stats grid */
  .stats {{
    display: grid; grid-template-columns: 2fr 1fr 1fr;
    gap: 1px; background: var(--border);
    border: 1px solid var(--border);
    overflow: hidden;
    margin: 0;
    border-radius: 6px 6px 0 0;
  }}
  .stats.secondary {{
    grid-template-columns: 1fr 1fr 1fr 1fr;
    margin-top: 1px; margin-bottom: 2.5rem;
    border-top: none; border-radius: 0 0 6px 6px;
  }}
  @media (max-width: 780px) {{
    .stats {{ grid-template-columns: 1fr 1fr; }}
    .stats.secondary {{ grid-template-columns: 1fr 1fr; }}
  }}
  .stat {{ background: var(--bg-card); padding: 1.5rem; position: relative; }}
  .stat.hero {{ background: linear-gradient(135deg, var(--bg-card), var(--bg-elev)); }}
  .stat-label {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.65rem; font-weight: 500;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--text-faint); margin-bottom: 0.75rem;
  }}
  .stat-value {{
    font-family: 'Manrope', sans-serif;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.04em;
    line-height: 1; color: var(--text);
  }}
  .stat.hero .stat-value {{
    font-size: clamp(2.75rem, 6.5vw, 4rem);
    background: linear-gradient(90deg, var(--cyan) 0%, var(--teal) 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .stat:not(.hero) .stat-value {{ font-size: 1.5rem; }}
  .stat-value.grad-violet {{
    background: linear-gradient(90deg, var(--violet) 0%, var(--pink) 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .stat-value.grad-amber {{
    background: linear-gradient(90deg, var(--amber) 0%, var(--orange) 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .stat-sub {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; color: var(--text-dim);
    margin-top: 0.6rem; letter-spacing: 0.05em;
  }}
  .benchmark-pill {{
    display: inline-block;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.6rem; font-weight: 600;
    letter-spacing: 0.15em; text-transform: uppercase;
    padding: 0.2rem 0.5rem;
    border-radius: 3px;
    margin-top: 0.6rem;
  }}
  .benchmark-pill.below {{ color: var(--orange); background: rgba(249, 115, 22, 0.12); }}
  .benchmark-pill.at {{ color: var(--amber); background: rgba(251, 191, 36, 0.12); }}
  .benchmark-pill.above {{ color: var(--positive); background: rgba(74, 222, 128, 0.12); }}

  /* Chart */
  .chart-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1.5rem 1.5rem 1rem;
    margin-bottom: 2.5rem;
  }}
  .chart-head {{
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 1rem; flex-wrap: wrap; gap: 0.75rem;
  }}
  .chart-title {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; letter-spacing: 0.18em;
    text-transform: uppercase; color: var(--text-dim);
  }}
  .chart-controls {{ display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap; }}
  .range-pills {{ display: flex; gap: 0; background: var(--bg-elev); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; }}
  .range-pill {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; letter-spacing: 0.1em;
    text-transform: uppercase; color: var(--text-dim);
    background: transparent; border: none;
    padding: 0.4rem 0.8rem; cursor: pointer;
    transition: all 0.15s ease;
  }}
  .range-pill:not(:last-child) {{ border-right: 1px solid var(--border); }}
  .range-pill:hover {{ color: var(--text); background: rgba(34,211,238,0.06); }}
  .range-pill.active {{ color: var(--cyan); background: var(--accent-soft); }}
  .chart-actions a {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; letter-spacing: 0.12em;
    text-transform: uppercase; color: var(--text-dim);
    text-decoration: none;
    padding: 0.4rem 0.75rem;
    border: 1px solid var(--border);
    border-radius: 4px; margin-left: 0.5rem;
    transition: all 0.15s ease;
  }}
  .chart-actions a:hover {{
    color: var(--cyan); border-color: var(--cyan);
    background: var(--accent-soft);
  }}
  svg#chart {{ display: block; width: 100%; height: 320px; }}

  /* Table */
  .table-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px; overflow: hidden;
  }}
  .table-head {{
    padding: 1.25rem 1.5rem;
    border-bottom: 1px solid var(--border);
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; letter-spacing: 0.18em;
    text-transform: uppercase; color: var(--text-dim);
  }}
  .table-scroll {{ max-height: 540px; overflow-y: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-family: 'IBM Plex Mono', monospace; font-size: 0.85rem; }}
  th {{
    text-align: left; padding: 0.75rem 1.5rem;
    font-weight: 500; font-size: 0.7rem;
    letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--text-faint);
    border-bottom: 1px solid var(--border);
    background: var(--bg-elev);
    position: sticky; top: 0; z-index: 1;
  }}
  td {{
    padding: 0.9rem 1.5rem;
    border-bottom: 1px solid var(--border);
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: var(--bg-elev); }}
  .idx {{ color: var(--text-faint); width: 3rem; }}
  .num {{ text-align: right; }}
  .num.delta {{ color: var(--positive); }}
  .tstack {{ display: flex; flex-direction: column; gap: 0.1rem; }}
  .tdate {{ color: var(--text); }}
  .ttime {{ color: var(--text-faint); font-size: 0.75rem; }}
  .empty {{ text-align: center; color: var(--text-faint); padding: 2.5rem; font-style: italic; }}

  .footer {{
    margin-top: 3rem; padding-top: 1.5rem;
    border-top: 1px solid var(--border);
    display: flex; justify-content: space-between;
    flex-wrap: wrap; gap: 0.5rem;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem; color: var(--text-faint);
    letter-spacing: 0.05em;
  }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <div class="brand">
      <div class="brand-dot"></div>
      <span class="brand-label">Live Tracker</span>
    </div>
    <div class="header-cluster">
      <div class="header-meta">
        <span class="header-meta-row"><strong>{readings_count}</strong> readings logged</span>
        <span class="header-meta-row">next reading in <strong>~{next_reading_min}m</strong></span>
      </div>
      <div class="h-plus">
        <span class="h-plus-value">H+{h_plus:02d}</span>
        <span class="h-plus-label">hours since launch</span>
      </div>
    </div>
  </div>

  <div class="title-block">
    <h1 class="product-title">{PRODUCT_TITLE}</h1>
    <p class="product-subtitle">{PRODUCT_SUBTITLE}</p>
  </div>

  <div class="trailer-card">
    <a class="trailer-thumb" href="{VIDEO_URL}" target="_blank" rel="noopener">
      <span class="live-badge"><span class="live-badge-dot"></span>Live Tracking</span>
      <img src="{thumbnail_url}" onerror="this.onerror=null;this.src='{fallback_thumb}'" alt="Trailer thumbnail">
    </a>
    <div class="trailer-info">
      <div>
        <div class="trailer-eyebrow">Currently Tracking</div>
        <h2 class="trailer-title">{CURRENT_TITLE}</h2>
        <div class="trailer-channel">{yt_title}{f' · {channel}' if channel else ''}</div>
      </div>
      <div class="trailer-meta">
        <span><strong>Published</strong> · {pub_display or '—'}</span>
        <span><strong>Tracking since</strong> · {first_ts[:16].replace('T', ' ') if first_ts else '—'} UTC</span>
      </div>
      <a class="trailer-link" href="{VIDEO_URL}" target="_blank" rel="noopener">Watch on YouTube ↗</a>
    </div>
  </div>

  <div class="stats">
    <div class="stat hero">
      <div class="stat-label">Views</div>
      <div class="stat-value">{latest_views_str}</div>
      <div class="stat-sub">Last reading · {latest_ts[:16].replace('T', ' ') if latest_ts else '—'} UTC</div>
    </div>
    <div class="stat">
      <div class="stat-label">Likes</div>
      <div class="stat-value grad-violet">{latest_likes_str}</div>
      <div class="stat-sub">cumulative engagement</div>
    </div>
    <div class="stat">
      <div class="stat-label">Comments</div>
      <div class="stat-value grad-violet">{latest_comments_str}</div>
      <div class="stat-sub">cumulative engagement</div>
    </div>
  </div>

  <div class="stats secondary">
    <div class="stat">
      <div class="stat-label">Last Hour Δ</div>
      <div class="stat-value grad-amber">{last_delta_str}</div>
      <div class="stat-sub">views / hour</div>
    </div>
    <div class="stat">
      <div class="stat-label">Avg Rate</div>
      <div class="stat-value">{avg_str}</div>
      <div class="stat-sub">views / hour since launch</div>
    </div>
    <div class="stat">
      <div class="stat-label">Peak Rate</div>
      <div class="stat-value grad-amber">{peak_str}</div>
      <div class="stat-sub">highest hourly velocity</div>
    </div>
    <div class="stat">
      <div class="stat-label">Engagement Rate</div>
      <div class="stat-value">{engagement_str}</div>
      <div class="stat-sub">(likes + comments) / views x 100</div>
    </div>
  </div>

  <div class="chart-card">
    <div class="chart-head">
      <div class="chart-title">Cumulative Views</div>
      <div class="chart-controls">
        <div class="range-pills" id="rangePills">
          <button class="range-pill" data-range="6">Last 6h</button>
          <button class="range-pill" data-range="24">Last 24h</button>
          <button class="range-pill active" data-range="all">All Time</button>
        </div>
        <span class="chart-actions">
          <a href="view_counts.csv" download>CSV</a>
          <a href=".">Refresh</a>
        </span>
      </div>
    </div>
    <svg id="chart" viewBox="0 0 1100 320" preserveAspectRatio="none"></svg>
  </div>

  <div class="table-card">
    <div class="table-head">Reading Log</div>
    <div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Timestamp</th>
          <th class="num">Views</th>
          <th class="num">Δ Views</th>
          <th class="num">Likes</th>
          <th class="num">Comments</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
    </div>
  </div>

  <div class="footer">
    <span>Auto-updated hourly via GitHub Actions + YouTube Data API</span>
    <span>Tracking since {first_ts[:16].replace('T', ' ') if first_ts else '—'} UTC</span>
    <span>Data Notes: Some hourly readings are estimates, used when automated tracking fell behind. Estimated values match the observed growth pattern and always stay consistent with the real measured readings around them.</span>
  </div>

</div>

<script>
const allPoints = {chart_data_json};

function drawChart(rangeHours) {{
  const svg = document.getElementById('chart');
  let points;
  if (rangeHours === 'all') {{
    points = allPoints;
  }} else {{
    const maxH = allPoints.length - 1;
    const cutoff = maxH - parseInt(rangeHours, 10);
    points = allPoints.filter(p => p.i >= cutoff);
  }}

  if (points.length < 2) {{
    svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#5a6676" font-family="IBM Plex Mono, monospace" font-size="12" letter-spacing="2">NOT ENOUGH DATA IN THIS RANGE</text>';
    return;
  }}

  const W = 1100, H = 320, padL = 75, padR = 20, padT = 20, padB = 40;
  const minI = points[0].i;
  const maxI = points[points.length - 1].i;
  const maxV = Math.max(...points.map(p => p.v));
  const minV = Math.min(...points.map(p => p.v));
  const range = Math.max(1, maxV - minV);
  const padY = range * 0.1;
  const yMin = Math.max(0, minV - padY);
  const yMax = maxV + padY;
  const yRange = Math.max(1, yMax - yMin);

  const xScale = i => padL + ((i - minI) / Math.max(1, maxI - minI)) * (W - padL - padR);
  const yScale = v => (H - padB) - ((v - yMin) / yRange) * (H - padT - padB);

  const gridLines = [];
  const gridCount = 4;
  for (let g = 0; g <= gridCount; g++) {{
    const y = padT + (g / gridCount) * (H - padT - padB);
    const val = Math.round(yMax - (g / gridCount) * yRange);
    gridLines.push(
      '<line x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y + '" stroke="#1c2430" stroke-width="1"/>' +
      '<text x="' + (padL - 12) + '" y="' + (y + 4) + '" text-anchor="end" fill="#5a6676" font-family="IBM Plex Mono, monospace" font-size="10">' + val.toLocaleString() + '</text>'
    );
  }}

  // Smart x-axis labels: show at most 6 labels, spread evenly
  const xLabels = [];
  const xLabelCount = Math.min(points.length, 6);
  for (let xi = 0; xi < xLabelCount; xi++) {{
    const idx = Math.round((xi / Math.max(1, xLabelCount - 1)) * (points.length - 1));
    const p = points[idx];
    if (!p) continue;
    const x = xScale(p.i);
    const label = 'H+' + String(p.i).padStart(2, '0');
    xLabels.push(
      '<text x="' + x + '" y="' + (H - padB + 20) + '" text-anchor="middle" fill="#5a6676" font-family="IBM Plex Mono, monospace" font-size="10">' + label + '</text>'
    );
  }}

  let areaPath = 'M' + xScale(points[0].i) + ' ' + (H - padB) + ' ';
  points.forEach(p => {{ areaPath += 'L' + xScale(p.i) + ' ' + yScale(p.v) + ' '; }});
  areaPath += 'L' + xScale(points[points.length - 1].i) + ' ' + (H - padB) + ' Z';

  let linePath = '';
  points.forEach((p, idx) => {{
    linePath += (idx === 0 ? 'M' : 'L') + xScale(p.i) + ' ' + yScale(p.v) + ' ';
  }});

  // Scale dot size based on density
  const dotR = points.length > 30 ? 2.5 : points.length > 15 ? 3 : 3.5;
  const dots = points.map(p =>
    '<circle cx="' + xScale(p.i) + '" cy="' + yScale(p.v) + '" r="' + dotR + '" fill="#07090c" stroke="#22d3ee" stroke-width="2">' +
    '<title>H+' + p.i + ' · ' + p.v.toLocaleString() + ' views · ' + p.t + '</title></circle>'
  ).join('');

  svg.innerHTML =
    '<defs>' +
      '<linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="#22d3ee" stop-opacity="0.28"/>' +
        '<stop offset="100%" stop-color="#14b8a6" stop-opacity="0"/>' +
      '</linearGradient>' +
      '<linearGradient id="lineGrad" x1="0" y1="0" x2="1" y2="0">' +
        '<stop offset="0%" stop-color="#22d3ee"/>' +
        '<stop offset="100%" stop-color="#14b8a6"/>' +
      '</linearGradient>' +
    '</defs>' +
    gridLines.join('') +
    '<path d="' + areaPath + '" fill="url(#areaGrad)"/>' +
    '<path d="' + linePath + '" fill="none" stroke="url(#lineGrad)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
    dots +
    xLabels.join('');
}}

// Wire up range pills
document.getElementById('rangePills').addEventListener('click', (e) => {{
  if (!e.target.classList.contains('range-pill')) return;
  document.querySelectorAll('.range-pill').forEach(p => p.classList.remove('active'));
  e.target.classList.add('active');
  drawChart(e.target.dataset.range);
}});

// Initial draw
drawChart('all');
</script>
</body>
</html>
"""


def main():
    now_utc_dt = datetime.now(timezone.utc)
    now_utc = now_utc_dt.isoformat(timespec="seconds")

    existing = read_rows(OUTPUT_CSV)
    if existing:
        try:
            last_ts = datetime.fromisoformat(existing[-1]["timestamp_utc"])
            mins_since_last = (now_utc_dt - last_ts).total_seconds() / 60
            if mins_since_last < 40:
                print(f"SKIP  {now_utc}  last reading was {mins_since_last:.0f}m ago (<40m)")
                # Still regenerate HTML so dashboard gets any CSS/config updates
                try:
                    meta = fetch_stats(VIDEO_URL)
                except Exception:
                    meta = {"title": existing[-1].get("title", ""), "channel": "", "published_at": ""}
                with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
                    f.write(build_html(existing, meta))
                return
        except (ValueError, KeyError):
            pass

    try:
        stats = fetch_stats(VIDEO_URL)
        row = {
            "timestamp_utc": now_utc,
            "view_count": stats["view_count"],
            "like_count": stats["like_count"] if stats["like_count"] is not None else "",
            "comment_count": stats["comment_count"] if stats["comment_count"] is not None else "",
            "title": stats["title"],
        }
        append_row(OUTPUT_CSV, row)
        print(f"OK  {now_utc}  views={stats['view_count']:,}  likes={stats['like_count']}  comments={stats['comment_count']}")
        meta = stats
    except Exception as e:
        row = {
            "timestamp_utc": now_utc, "view_count": "", "like_count": "",
            "comment_count": "", "title": f"ERROR: {e}",
        }
        append_row(OUTPUT_CSV, row)
        print(f"ERROR  {now_utc}  {e}")
        meta = {"title": "", "channel": "", "published_at": ""}

    rows = read_rows(OUTPUT_CSV)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(rows, meta))
    print(f"Updated {OUTPUT_HTML} with {len(rows)} readings.")


if __name__ == "__main__":
    main()
