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
VIDEO_URL = "https://www.youtube.com/watch?v=5xQ2LZCknfc"
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
        params={"part": "statistics,snippet", "id": video_id, "key": API_KEY},
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
        "hours_elapsed": None,
    }
    if not valid:
        return stats

    latest = valid[-1]
    stats["latest_views"] = latest["v"]
    stats["latest_likes"] = latest["l"]
    stats["latest_comments"] = latest["c"]

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


def build_html(rows: list) -> str:
    derived = compute_derived(rows)
    video_title = rows[-1].get("title", "") if rows else ""

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
            chart_points.append({"i": i - 1, "v": v, "t": r.get("timestamp_utc", "")})

        ts = r.get("timestamp_utc", "")
        try:
            dt = datetime.fromisoformat(ts)
            date_str = dt.strftime("%b %-d").replace("%-d", str(dt.day))
            time_str = dt.strftime("%H:%M")
        except Exception:
            date_str, time_str = ts, ""

        trs.append(
            f'<tr><td class="idx">{i}</td>'
            f'<td><div class="tdate">{date_str}</div><div class="ttime">{time_str} UTC</div></td>'
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
    hours_elapsed = derived["hours_elapsed"] if derived["hours_elapsed"] is not None else 0
    readings_count = len(rows)

    latest_ts = rows[-1]["timestamp_utc"] if rows else ""
    first_ts = rows[0]["timestamp_utc"] if rows else ""
    title_display = video_title or "Awaiting first reading"

    today_str = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y").replace("%-d", str(datetime.now(timezone.utc).day))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>View Tracker — {title_display}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700;900&family=Source+Serif+4:ital,wght@0,400;0,500;0,600;1,400&family=Source+Sans+3:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --paper: #fdfcf8;
    --paper-shade: #f5f3ec;
    --rule: #d9d6cc;
    --ink: #1a1a1a;
    --ink-soft: #3d3d3d;
    --ink-faint: #6b6b6b;
    --ink-quiet: #9a9a94;
    --accent: #a01818;
    --positive: #1f6b3a;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0; padding: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: 'Source Serif 4', Georgia, serif;
    -webkit-font-smoothing: antialiased;
    min-height: 100vh;
  }}
  .container {{ max-width: 960px; margin: 0 auto; padding: 3rem 2rem 4rem; }}

  .masthead {{
    text-align: center;
    border-bottom: 3px double var(--ink);
    padding-bottom: 1.5rem;
    margin-bottom: 2rem;
  }}
  .mast-label {{
    font-family: 'Source Sans 3', sans-serif;
    font-size: 0.7rem;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-bottom: 0.75rem;
  }}
  .mast-title {{
    font-family: 'Playfair Display', Georgia, serif;
    font-size: clamp(2.5rem, 6vw, 3.75rem);
    font-weight: 900;
    line-height: 1;
    letter-spacing: -0.01em;
    margin: 0;
  }}
  .mast-date {{
    font-family: 'Source Sans 3', sans-serif;
    font-size: 0.75rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-top: 0.75rem;
  }}

  .article-header {{
    text-align: center;
    margin-bottom: 2.5rem;
  }}
  .kicker {{
    font-family: 'Source Sans 3', sans-serif;
    font-size: 0.7rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--accent);
    font-weight: 600;
    margin-bottom: 0.75rem;
  }}
  h1.headline {{
    font-family: 'Playfair Display', Georgia, serif;
    font-weight: 700;
    font-size: clamp(1.5rem, 3.5vw, 2.25rem);
    line-height: 1.2;
    letter-spacing: -0.01em;
    margin: 0 auto;
    max-width: 700px;
  }}
  .standfirst {{
    font-family: 'Source Serif 4', serif;
    font-style: italic;
    color: var(--ink-soft);
    font-size: 1rem;
    margin-top: 1rem;
    max-width: 600px;
    margin-left: auto;
    margin-right: auto;
    line-height: 1.5;
  }}

  .stats {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
    margin-bottom: 2.5rem;
  }}
  .stats.secondary {{
    grid-template-columns: repeat(3, 1fr);
    border-top: none;
    margin-top: -2.5rem;
    margin-bottom: 2.5rem;
    border-bottom: 1px solid var(--rule);
  }}
  @media (max-width: 720px) {{
    .stats, .stats.secondary {{ grid-template-columns: 1fr 1fr; }}
  }}
  .stat {{
    padding: 1.5rem 1rem;
    text-align: center;
    border-right: 1px solid var(--rule);
  }}
  .stat:last-child {{ border-right: none; }}
  .stat-label {{
    font-family: 'Source Sans 3', sans-serif;
    font-size: 0.65rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-bottom: 0.6rem;
  }}
  .stat-value {{
    font-family: 'Playfair Display', serif;
    font-weight: 700;
    font-size: 2rem;
    line-height: 1;
    color: var(--ink);
    font-variant-numeric: tabular-nums;
  }}
  .stat.hero .stat-value {{ font-size: 2.75rem; color: var(--accent); }}
  .stat-sub {{
    font-family: 'Source Serif 4', serif;
    font-style: italic;
    font-size: 0.8rem;
    color: var(--ink-faint);
    margin-top: 0.5rem;
  }}

  .section-title {{
    font-family: 'Playfair Display', serif;
    font-weight: 600;
    font-size: 1rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--ink);
    border-bottom: 2px solid var(--ink);
    padding-bottom: 0.5rem;
    margin-bottom: 1.25rem;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }}
  .section-actions a {{
    font-family: 'Source Sans 3', sans-serif;
    font-size: 0.7rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--ink-faint);
    text-decoration: none;
    margin-left: 1rem;
    border-bottom: 1px solid var(--ink-quiet);
    padding-bottom: 2px;
    transition: color 0.15s ease;
  }}
  .section-actions a:hover {{ color: var(--accent); border-color: var(--accent); }}

  .chart-wrap {{ margin-bottom: 3rem; }}
  svg#chart {{ display: block; width: 100%; height: 340px; }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-family: 'Source Serif 4', serif;
    font-size: 0.9rem;
  }}
  thead th {{
    text-align: left;
    padding: 0.6rem 0.75rem;
    font-family: 'Source Sans 3', sans-serif;
    font-weight: 600;
    font-size: 0.7rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-faint);
    border-bottom: 1px solid var(--ink);
  }}
  tbody td {{
    padding: 0.7rem 0.75rem;
    border-bottom: 1px solid var(--rule);
    color: var(--ink);
    font-variant-numeric: tabular-nums;
    vertical-align: middle;
  }}
  tbody tr:hover td {{ background: var(--paper-shade); }}
  .idx {{ color: var(--ink-quiet); width: 2.5rem; font-family: 'Source Sans 3', sans-serif; font-size: 0.8rem; }}
  .tdate {{ font-weight: 500; }}
  .ttime {{ color: var(--ink-faint); font-size: 0.8rem; font-style: italic; }}
  .num {{ text-align: right; font-family: 'Source Sans 3', sans-serif; }}
  .num.delta {{ color: var(--positive); }}
  .empty {{ text-align: center; color: var(--ink-quiet); padding: 2.5rem; font-style: italic; }}

  .colophon {{
    margin-top: 3rem;
    padding-top: 1.5rem;
    border-top: 1px solid var(--rule);
    font-family: 'Source Sans 3', sans-serif;
    font-size: 0.75rem;
    color: var(--ink-quiet);
    text-align: center;
    letter-spacing: 0.03em;
    line-height: 1.6;
  }}
</style>
</head>
<body>
<div class="container">

  <div class="masthead">
    <div class="mast-label">The Trailer Tracker</div>
    <h1 class="mast-title">Views &amp; Signals</h1>
    <div class="mast-date">{today_str}  ·  {readings_count} Readings  ·  {hours_elapsed}h Elapsed</div>
  </div>

  <div class="article-header">
    <div class="kicker">Currently Tracking</div>
    <h1 class="headline">{title_display}</h1>
    <p class="standfirst">An hourly record of public engagement, captured via the YouTube Data API and updated automatically.</p>
  </div>

  <div class="stats">
    <div class="stat hero">
      <div class="stat-label">Views</div>
      <div class="stat-value">{latest_views_str}</div>
      <div class="stat-sub">most recent reading</div>
    </div>
    <div class="stat">
      <div class="stat-label">Likes</div>
      <div class="stat-value">{latest_likes_str}</div>
      <div class="stat-sub">cumulative</div>
    </div>
    <div class="stat">
      <div class="stat-label">Comments</div>
      <div class="stat-value">{latest_comments_str}</div>
      <div class="stat-sub">cumulative</div>
    </div>
  </div>

  <div class="stats secondary">
    <div class="stat">
      <div class="stat-label">Last Hour</div>
      <div class="stat-value" style="font-size:1.5rem">{last_delta_str}</div>
      <div class="stat-sub">views / hour</div>
    </div>
    <div class="stat">
      <div class="stat-label">Average Pace</div>
      <div class="stat-value" style="font-size:1.5rem">{avg_str}</div>
      <div class="stat-sub">views / hour since launch</div>
    </div>
    <div class="stat">
      <div class="stat-label">Peak Hour</div>
      <div class="stat-value" style="font-size:1.5rem">{peak_str}</div>
      <div class="stat-sub">highest velocity observed</div>
    </div>
  </div>

  <div class="chart-wrap">
    <div class="section-title">
      <span>Cumulative View Count</span>
      <span class="section-actions">
        <a href="view_counts.csv" download>Download data</a>
        <a href=".">Refresh</a>
      </span>
    </div>
    <svg id="chart" viewBox="0 0 1100 340" preserveAspectRatio="none"></svg>
  </div>

  <div class="section-title"><span>Reading Log</span></div>
  <table>
    <thead>
      <tr>
        <th>№</th>
        <th>Timestamp</th>
        <th class="num">Views</th>
        <th class="num">Δ Views</th>
        <th class="num">Likes</th>
        <th class="num">Comments</th>
      </tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>

  <div class="colophon">
    Auto-updated hourly via GitHub Actions and the YouTube Data API.<br>
    Tracking since {first_ts[:16].replace('T', ' ') if first_ts else '—'} UTC  ·  Latest reading {latest_ts[:16].replace('T', ' ') if latest_ts else '—'} UTC
  </div>

</div>

<script>
const points = {chart_data_json};
const svg = document.getElementById('chart');
if (points.length < 2) {{
  svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#9a9a94" font-family="Source Serif 4, Georgia, serif" font-style="italic" font-size="14">Awaiting second reading…</text>';
}} else {{
  const W = 1100, H = 340, padL = 75, padR = 20, padT = 20, padB = 40;
  const maxI = Math.max(...points.map(p => p.i));
  const maxV = Math.max(...points.map(p => p.v));
  const minV = Math.min(...points.map(p => p.v));
  const range = Math.max(1, maxV - minV);
  const padY = range * 0.1;
  const yMin = Math.max(0, minV - padY);
  const yMax = maxV + padY;
  const yRange = Math.max(1, yMax - yMin);
  const xScale = i => padL + (i / Math.max(1, maxI)) * (W - padL - padR);
  const yScale = v => (H - padB) - ((v - yMin) / yRange) * (H - padT - padB);

  const gridLines = [];
  const gridCount = 4;
  for (let g = 0; g <= gridCount; g++) {{
    const y = padT + (g / gridCount) * (H - padT - padB);
    const val = Math.round(yMax - (g / gridCount) * yRange);
    gridLines.push(
      `<line x1="${{padL}}" y1="${{y}}" x2="${{W - padR}}" y2="${{y}}" stroke="#d9d6cc" stroke-width="1" stroke-dasharray="2,3"/>` +
      `<text x="${{padL - 12}}" y="${{y + 4}}" text-anchor="end" fill="#6b6b6b" font-family="Source Sans 3, sans-serif" font-size="11">${{val.toLocaleString()}}</text>`
    );
  }}

  const xLabels = [];
  const xLabelCount = Math.min(points.length, 6);
  for (let xi = 0; xi < xLabelCount; xi++) {{
    const idx = Math.round((xi / Math.max(1, xLabelCount - 1)) * maxI);
    const p = points[idx];
    if (!p) continue;
    const x = xScale(p.i);
    const label = String(p.i + 1);
    xLabels.push(
      `<text x="${{x}}" y="${{H - padB + 22}}" text-anchor="middle" fill="#6b6b6b" font-family="Source Sans 3, sans-serif" font-size="11">${{label}}</text>`
    );
  }}

  let areaPath = 'M' + xScale(points[0].i) + ' ' + (H - padB) + ' ';
  points.forEach(p => {{ areaPath += 'L' + xScale(p.i) + ' ' + yScale(p.v) + ' '; }});
  areaPath += 'L' + xScale(points[points.length - 1].i) + ' ' + (H - padB) + ' Z';

  let linePath = '';
  points.forEach((p, idx) => {{
    linePath += (idx === 0 ? 'M' : 'L') + xScale(p.i) + ' ' + yScale(p.v) + ' ';
  }});

  const dots = points.map(p =>
    `<circle cx="${{xScale(p.i)}}" cy="${{yScale(p.v)}}" r="3" fill="#a01818"><title>${{p.t}} — ${{p.v.toLocaleString()}} views</title></circle>`
  ).join('');

  svg.innerHTML =
    `<defs><linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#a01818" stop-opacity="0.12"/>
      <stop offset="100%" stop-color="#a01818" stop-opacity="0"/>
    </linearGradient></defs>` +
    gridLines.join('') +
    `<path d="${{areaPath}}" fill="url(#areaGrad)"/>` +
    `<path d="${{linePath}}" fill="none" stroke="#a01818" stroke-width="1.75" stroke-linejoin="round"/>` +
    dots +
    xLabels.join('') +
    `<text x="${{padL - 55}}" y="${{padT + 5}}" fill="#9a9a94" font-family="Source Sans 3, sans-serif" font-size="9" letter-spacing="1.5" text-transform="uppercase">VIEWS</text>` +
    `<text x="${{W / 2}}" y="${{H - 5}}" text-anchor="middle" fill="#9a9a94" font-family="Source Sans 3, sans-serif" font-size="9" letter-spacing="1.5">READING NUMBER</text>`;
}}
</script>
</body>
</html>
"""


def main():
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
    except Exception as e:
        row = {
            "timestamp_utc": now_utc, "view_count": "", "like_count": "",
            "comment_count": "", "title": f"ERROR: {e}",
        }
        append_row(OUTPUT_CSV, row)
        print(f"ERROR  {now_utc}  {e}")

    rows = read_rows(OUTPUT_CSV)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(rows))
    print(f"Updated {OUTPUT_HTML} with {len(rows)} readings.")


if __name__ == "__main__":
    main()
