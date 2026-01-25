import os
import json
import time
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from mdlm_config import db_path, load_env

# =========================================================
# ENV
# =========================================================
load_env(override=True)

SQLITE_DB_PATH = str(db_path())

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
)
KUGOU_COOKIE = os.getenv("KUGOU_COOKIE", "") or ""

FETCH_TIMEOUT = float(os.getenv("FETCH_TIMEOUT", "20"))
FETCH_RETRY = int(os.getenv("FETCH_RETRY", "3"))
FETCH_SLEEP = float(os.getenv("FETCH_SLEEP", "1.2"))

# =========================================================
# 目标榜单名（从 kugou rank/list 里按名字匹配）
# =========================================================
KUGOU_TARGETS = [
    "酷狗热歌榜",
    "酷狗新歌榜",
    "酷狗飙升榜",
]

# =========================================================
# DB helpers
# =========================================================
def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()

def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return [r[1] for r in rows]

def ensure_platform(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM platform WHERE name=?", (name,)).fetchone()
    if row:
        return int(row[0])
    conn.execute("INSERT INTO platform(name) VALUES(?)", (name,))
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

def ensure_chart(conn: sqlite3.Connection, platform_id: int, name: str, source_key: str) -> int:
    row = conn.execute(
        "SELECT id FROM chart WHERE platform_id=? AND name=?",
        (platform_id, name),
    ).fetchone()
    if row:
        return int(row[0])

    conn.execute(
        "INSERT INTO chart(platform_id, name, source_key) VALUES(?,?,?)",
        (platform_id, name, source_key),
    )
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

def insert_snapshot(conn: sqlite3.Connection, chart_id: int, captured_at: str, top_n: int, raw_text: Optional[str]) -> int:
    """
    兼容 chart_snapshot 不同 schema：
    - 必有：chart_id, captured_at
    - 可能有：top_n (NOT NULL), raw / raw_json / payload_json / raw_text
    """
    cols = set(table_columns(conn, "chart_snapshot"))

    payload: Dict[str, Any] = {"chart_id": chart_id, "captured_at": captured_at}

    if "top_n" in cols:
        payload["top_n"] = int(top_n)

    raw_col_candidates = ["raw", "raw_json", "payload_json", "raw_text"]
    raw_col = None
    for c in raw_col_candidates:
        if c in cols:
            raw_col = c
            break
    if raw_col:
        payload[raw_col] = raw_text

    keys = ",".join(payload.keys())
    qs = ",".join(["?"] * len(payload))
    conn.execute(f"INSERT INTO chart_snapshot({keys}) VALUES ({qs})", tuple(payload.values()))
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

def insert_entries(conn: sqlite3.Connection, snapshot_id: int, rows: List[Dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT INTO chart_entry(snapshot_id, rank, track_platform_id, track_name, artist_name_raw, score, extra_metrics)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_id,
                int(r["rank"]),
                str(r["track_platform_id"]),
                str(r["track_name"]),
                (r.get("artist_name_raw") or ""),
                None,
                json.dumps(r.get("extra_metrics") or {}, ensure_ascii=False),
            )
            for r in rows
        ],
    )
    conn.commit()

# =========================================================
# HTTP helpers
# =========================================================
def make_client() -> httpx.Client:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Referer": "https://www.kugou.com/",
    }
    if KUGOU_COOKIE:
        headers["Cookie"] = KUGOU_COOKIE
    return httpx.Client(timeout=FETCH_TIMEOUT, headers=headers)

def extract_json_from_kugou_text(text: str) -> Dict[str, Any]:
    """
    酷狗接口经常返回 text/html，并用 <!--KG_TAG_RES_START--> 包裹 JSON。
    """
    if not text:
        raise ValueError("empty response")

    try:
        return json.loads(text)
    except Exception:
        pass

    marker = "<!--KG_TAG_RES_START-->"
    if marker in text:
        text = text.split(marker, 1)[1]

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("cannot locate json object in response")

    return json.loads(text[start : end + 1])

def get_json(client: httpx.Client, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    last_err = None
    for attempt in range(FETCH_RETRY):
        try:
            r = client.get(url, params=params)
            r.raise_for_status()
            data = extract_json_from_kugou_text(r.text)
            if not isinstance(data, dict):
                raise ValueError("response json is not dict")
            return data
        except Exception as e:
            last_err = e
            time.sleep(FETCH_SLEEP * (attempt + 1))
    raise RuntimeError(f"Kugou fetch failed: {url} params={params} err={last_err}")

# =========================================================
# Kugou API (mobilecdnbj.kugou.com)
# =========================================================
def kugou_rank_list(client: httpx.Client) -> List[Dict[str, Any]]:
    url = "http://mobilecdnbj.kugou.com/api/v3/rank/list"
    data = get_json(client, url, params={"withsong": "0"})
    info = (((data or {}).get("data") or {}).get("info")) or []
    if not isinstance(info, list) or not info:
        raise RuntimeError("Kugou rank/list empty.")
    return info

def _pick_latest_volid_from_vol_list(vols: Any, rankid: int) -> int:
    """
    vols 可能是：
    A) 直接 list[ {volid,...}, ... ]   ✅ 你 curl 就是这种
    B) year_blocks: list[ {year:..., vols:[{volid...},...]}, ... ]
    """
    # A: 直接 vols = [ {volid}, ... ]
    if isinstance(vols, list) and vols and isinstance(vols[0], dict) and vols[0].get("volid"):
        return int(vols[0]["volid"])

    # B: year_blocks = [ {year:..., vols:[...]}, ... ]
    if isinstance(vols, list) and vols and isinstance(vols[0], dict) and isinstance(vols[0].get("vols"), list):
        inner = vols[0].get("vols") or []
        if inner and isinstance(inner[0], dict) and inner[0].get("volid"):
            return int(inner[0]["volid"])

    raise RuntimeError(f"Kugou rank/vol missing volid for rankid={rankid}")

def kugou_rank_vol(client: httpx.Client, rankid: int) -> int:
    """
    真实结构（你 curl 已验证）：
    data.info[0] = {"year": 2026, "vols": [ {volid...}, ... ]}
    """
    url = "http://mobilecdnbj.kugou.com/api/v3/rank/vol"
    params = {
        "ranktype": "2",
        "plat": "0",
        "rankid": str(rankid),
        "with_res_tag": "1",
    }
    data = get_json(client, url, params=params)
    info = (((data or {}).get("data") or {}).get("info")) or []
    if not isinstance(info, list) or not info or not isinstance(info[0], dict):
        raise RuntimeError(f"Kugou rank/vol empty for rankid={rankid}")

    vols = info[0].get("vols")
    return _pick_latest_volid_from_vol_list(vols, rankid=rankid)

def kugou_rank_song(client: httpx.Client, rankid: int, volid: int, top_n: int) -> Tuple[str, List[Dict[str, Any]]]:
    url = "http://mobilecdnbj.kugou.com/api/v3/rank/song"
    params = {
        "rankid": str(rankid),
        "volid": str(volid),
        "pagesize": str(top_n),
        "page": "1",
        "plat": "0",
        "version": "9108",
        "area_code": "1",
        "with_res_tag": "1",
    }

    last_err = None
    for attempt in range(FETCH_RETRY):
        try:
            r = client.get(url, params=params)
            r.raise_for_status()
            raw = r.text
            data = extract_json_from_kugou_text(raw)
            info = (((data or {}).get("data") or {}).get("info")) or []
            if not isinstance(info, list) or not info:
                raise RuntimeError("Kugou rank/song empty list.")
            return raw, info
        except Exception as e:
            last_err = e
            time.sleep(FETCH_SLEEP * (attempt + 1))
    raise RuntimeError(f"Kugou rank/song failed: {last_err}")

def parse_kugou_songs(info: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    rank = 0
    for item in info:
        if len(out) >= limit:
            break
        if not isinstance(item, dict):
            continue

        filename = (item.get("filename") or "").strip()  # "艺人 - 歌名"
        songname = (item.get("songname") or "").strip()
        audio_id = item.get("audio_id") or item.get("audioId") or item.get("hash")

        track_name = ""
        artist_raw = ""

        if filename and " - " in filename:
            left, right = filename.split(" - ", 1)
            artist_raw = left.strip()
            track_name = right.strip()
        else:
            track_name = songname or filename or ""

        if not track_name:
            continue

        track_platform_id = str(audio_id) if audio_id else track_name

        rank += 1
        out.append(
            {
                "rank": rank,
                "track_platform_id": track_platform_id,
                "track_name": track_name,
                "artist_name_raw": artist_raw,
                "extra_metrics": {},
            }
        )

    if not out:
        raise RuntimeError("Kugou parse failed: no usable items.")
    return out

# =========================================================
# Main ingest
# =========================================================
def ingest_kugou(top_n: int = 100) -> None:
    db_path = Path(SQLITE_DB_PATH).resolve()
    if not db_path.exists():
        raise SystemExit(f"❌ 找不到 SQLite: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        platform_id = ensure_platform(conn, "酷狗音乐")

        with make_client() as client:
            ranks = kugou_rank_list(client)

            name_to_rankid: Dict[str, int] = {}
            for r in ranks:
                if not isinstance(r, dict):
                    continue
                rn = (r.get("rankname") or "").strip()
                rid = r.get("rankid")
                if rn and rid:
                    name_to_rankid[rn] = int(rid)

            resolved: List[Tuple[str, int]] = []
            for want in KUGOU_TARGETS:
                if want in name_to_rankid:
                    resolved.append((want, name_to_rankid[want]))
                    continue
                key = want.replace("酷狗", "")
                hit = None
                for k, v in name_to_rankid.items():
                    if key and key in k:
                        hit = (k, v)
                        break
                if hit:
                    resolved.append((want, hit[1]))

            if not resolved:
                raise RuntimeError("Kugou rank/list did not find target charts (热歌榜/新歌榜/飙升榜).")

            for chart_name, rankid in resolved:
                volid = kugou_rank_vol(client, rankid=rankid)
                raw, info = kugou_rank_song(client, rankid=rankid, volid=volid, top_n=top_n)
                rows = parse_kugou_songs(info, limit=top_n)

                chart_id = ensure_chart(
                    conn,
                    platform_id,
                    chart_name.replace("酷狗", ""),
                    source_key=f"kugou_rankid:{rankid}",
                )

                snapshot_id = insert_snapshot(conn, chart_id, now_iso(), top_n=top_n, raw_text=raw)
                insert_entries(conn, snapshot_id, rows)

                print(f"✅ 入库完成：酷狗音乐 - {chart_name.replace('酷狗','')} Top{len(rows)} (snapshot_id={snapshot_id})")

    finally:
        conn.close()

if __name__ == "__main__":
    ingest_kugou(top_n=int(os.getenv("SYNC_TOP_N", "100")))