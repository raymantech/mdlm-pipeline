import sqlite3
from notion_client import Client
from datetime import datetime

NOTION_TOKEN = "secret_xxx"
DATABASE_ID = "xxxxxxxxxxxxxxxx"

notion = Client(auth=NOTION_TOKEN)
conn = sqlite3.connect("charts.db")

rows = conn.execute("""
SELECT
  e.track_name,
  e.artist_name_raw,
  e.rank,
  cs.captured_at
FROM chart_entry e
JOIN chart_snapshot cs ON e.snapshot_id = cs.id
ORDER BY cs.id DESC, e.rank ASC
LIMIT 20
""").fetchall()

for track, artist, rank, captured_at in rows:
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "歌名": {"title": [{"text": {"content": track}}]},
            "艺人": {"rich_text": [{"text": {"content": artist or ""}}]},
            "排名": {"number": rank},
            "日期": {"date": {"start": captured_at[:10]}},
        }
    )

print("✅ Synced to Notion")