"""
Runs ONCE per GitHub Actions invocation. Fetches the current YouTube
view count for VIDEO_URL and appends a new row to view_counts.csv.
Also regenerates index.html (a simple dashboard page).
"""

import csv
import os
import re
from datetime import datetime, timezone

import requests

# -------- CONFIG --------
# Change this to the Clayface trailer URL when it drops.
VIDEO_URL = "https://www.youtube.com/watch?v=5xQ2LZCknfc"
OUTPUT_CSV = "view_counts.csv"
OUTPUT_HTML = "index.html"
# ------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

CSV_FIELDS = ["timestamp_utc", "view_count", "title"]


def fetch_stats(video_url: str) -> dict:
    r = requests.get(video_url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text

    view_match = re.search(r'"viewCount":"(\d+)"', html)
    if not view_match:
        raise RuntimeError("Could not find viewCount in page HTML")
    views = int(view_match.group(1))

    title_match = re.search(r'"title":"([^"]+)","lengthSeconds"', html)
    title = title_match.group(1) if title_match else ""

    return {"view_count": views, "title": title}


def append_row(path: str, row: dict) -> None:
    new_file = not os.path.exists(path)
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


def build_html(rows: list) -> str:
    """Generate a simple self-contained dashboard page."""
    if not rows:
        body_meta = "<p><em>No readings yet.</em></p>"
        table_rows = ""
        chart_data_json = "[]"
        video_title = ""
    else:
        latest = rows[-1]
        first = rows[0]
        video_title = latest.get("title", "")
        try:
            latest_views = int(latest["view_count"])
            latest_views_str = f"{latest_views:,}"
        except (ValueError, KeyError):
            latest_views_str = "—"

        body_meta = (
            f"<p><strong>Video:</strong> {video_title}<br>"
            f"<strong>Latest view count:</strong> {latest_views_str}<br>"
            f"<strong>Latest reading:</strong> {latest['timestamp_utc']} UTC<br>"
            f"<strong>Readings so far:</strong> {len(rows)}<br>"
            f"<strong>Tracking since:</strong> {first['timestamp_utc']} UTC</p>"
        )

        # Build table rows with delta vs previous
        prev = None
        trs = []
        for i, r in enumerate(rows, start=1):
            try:
                v = int(r["view_count"])
                v_str = f"{v:,}"
                delta_str = f"{v - prev:,}" if prev is not None else "—"
                prev = v
            except (ValueError, KeyError):
                v_str = "—"
                delta_str = "—"
            trs.append(
                f"<tr><td>{i}</td><td>{r['timestamp_utc']}</td>"
                f"<td class='num'>{v_str}</td><td class='num'>{delta_str}</td></tr>"
            )
        table_rows = "\n".join(trs)

        # Chart data
        import json as _json
        chart_points = []
        for i, r in enumerate(rows):
            try:
                chart_points.append({"i": i, "v": int(r["view_count"]), "t": r["timestamp_utc"]})
            except (ValueError, KeyError):
                pass
        chart_data_json = _json.dumps(chart_points)

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>YouTube View Tracker — {video_title or 'Clayface'}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; max-width: 900px; color: #222; }}
  h1 {{ margin-bottom: 0.25rem; }}
  p {{ line-height: 1.5; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; margin-top: 1rem; }}
  th, td {{ border: 1px solid #ddd; padding: 0.4rem 0.6rem; text-align: left; }}
  th {{ background: #f3f3f3; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .chart {{ margin: 1.5rem 0; border: 1px solid #ddd; padding: 0.5rem; }}
  .controls a {{ display: inline-block; padding: 0.4rem 0.8rem; border: 1px solid #888;
                 background: #fff; text-decoration: none; color: #000; border-radius: 3px;
                 margin-right: 0.5rem; }}
  .footer {{ margin-top: 2rem; color: #888; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>YouTube View Tracker</h1>
{body_meta}

<div class="controls">
  <a href="view_counts.csv" download>Download CSV</a>
  <a href=".">Refresh</a>
</div>

<div class="chart">
  <svg id="chart" width="100%" height="280" viewBox="0 0 900 280" preserveAspectRatio="none"></svg>
</div>

<table>
  <thead>
    <tr>
      <th>#</th>
      <th>Timestamp (UTC)</th>
      <th class="num">View count</th>
      <th class="num">Delta</th>
    </tr>
  </thead>
  <tbody>
    {table_rows}
  </tbody>
</table>

<p class="footer">
  Auto-updated hourly by GitHub Actions. View count is scraped from the public
  YouTube watch page; YouTube throttles and de-duplicates counts, so treat the
  curve shape as the signal rather than any single hour's value.
</p>

<script>
const points = {chart_data_json};
const svg = document.getElementById('chart');
if (points.length < 2) {{
  svg.innerHTML = '<text x="20" y="30" fill="#999">Need at least 2 readings to plot.</text>';
}} else {{
  const W = 900, H = 280, pad = 50;
  const maxI = Math.max(...points.map(p => p.i));
  const maxV = Math.max(...points.map(p => p.v));
  const minV = Math.min(...points.map(p => p.v));
  const range = Math.max(1, maxV - minV);
  const xScale = i => pad + (i / Math.max(1, maxI)) * (W - 2 * pad);
  const yScale = v => (H - pad) - ((v - minV) / range) * (H - 2 * pad);

  let path = '';
  points.forEach((p, idx) => {{
    path += (idx === 0 ? 'M' : 'L') + xScale(p.i) + ' ' + yScale(p.v) + ' ';
  }});
  const dots = points.map(p =>
    '<circle cx="' + xScale(p.i) + '" cy="' + yScale(p.v) + '" r="3" fill="#c00">' +
    '<title>' + p.t + ' — ' + p.v.toLocaleString() + ' views</title></circle>'
  ).join('');

  svg.innerHTML =
    '<line x1="' + pad + '" y1="' + (H - pad) + '" x2="' + (W - pad) + '" y2="' + (H - pad) + '" stroke="#888"/>' +
    '<line x1="' + pad + '" y1="' + pad + '" x2="' + pad + '" y2="' + (H - pad) + '" stroke="#888"/>' +
    '<text x="' + pad + '" y="' + (pad - 10) + '" font-size="11" fill="#666">' + maxV.toLocaleString() + ' views</text>' +
    '<text x="' + pad + '" y="' + (H - pad + 20) + '" font-size="11" fill="#666">reading 1</text>' +
    '<text x="' + (W - pad - 70) + '" y="' + (H - pad + 20) + '" font-size="11" fill="#666">reading ' + points.length + '</text>' +
    '<path d="' + path + '" fill="none" stroke="#c00" stroke-width="2"/>' +
    dots;
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
            "title": stats["title"],
        }
        append_row(OUTPUT_CSV, row)
        print(f"OK  {now_utc}  views={stats['view_count']:,}  title={stats['title'][:60]}")
    except Exception as e:
        row = {"timestamp_utc": now_utc, "view_count": "", "title": f"ERROR: {e}"}
        append_row(OUTPUT_CSV, row)
        print(f"ERROR  {now_utc}  {e}")

    rows = read_rows(OUTPUT_CSV)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(rows))
    print(f"Updated {OUTPUT_HTML} with {len(rows)} readings.")


if __name__ == "__main__":
    main()
