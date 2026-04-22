# Clayface YouTube View Tracker

Polls a YouTube video's public view count once per hour for 72 hours,
using GitHub Actions (free). Results are written to `view_counts.csv`
and a dashboard page at `index.html`.

## To change the tracked video

Edit the `VIDEO_URL` line at the top of `tracker.py`.

## To reset the data

Delete `view_counts.csv` (or edit it to keep only the header row).
