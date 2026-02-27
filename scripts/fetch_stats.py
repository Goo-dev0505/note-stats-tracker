import os
import time
import json
import csv
import sys
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

# Windows環境でのUnicode出力対応
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ===== 設定（環境変数から読み込み） =====
NOTE_COOKIE = os.environ.get("NOTE_COOKIE", "")
NOTE_USERNAME = os.environ.get("NOTE_USERNAME", "")
COOKIE_SET_DATE = os.environ.get("COOKIE_SET_DATE", "") # YYYY-MM-DD形式
BASE_URL = "https://note.com"
DATA_DIR = Path("data")
CACHE_PATH = DATA_DIR / "v3_dates_cache.json"
JST = timezone(timedelta(hours=9))

# ===== 1. バリデーション & 認証ロジック =====
def validate_setup():
    if not NOTE_COOKIE or not NOTE_USERNAME:
        print("🚨 NOTE_COOKIE または NOTE_USERNAME が未設定や！"); sys.exit(1)
    if "=" not in NOTE_COOKIE:
        print("🚨 NOTE_COOKIE の形式が不正や（key=value形式にしてな）"); sys.exit(1)

def verify_auth(session):
    print("🔑 認証チェック中...")
    url = f"{BASE_URL}/api/v1/stats/pv?filter=all&page=1&sort=pv"
    r = session.get(url, timeout=20)
    if r.status_code == 200 and "data" in r.json():
        print("✓ 認証OK（stats APIにアクセスできました）")
        return True
    else:
        print(f"🚨 認証失敗: HTTP {r.status_code}"); sys.exit(1)

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (GitHubActions; note-fetcher)",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE_URL}/{NOTE_USERNAME}",
    })
    cookies = {}
    for part in NOTE_COOKIE.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies[k] = v
    s.cookies.update(cookies)
    return s

# ===== 2. データ抽出ロジック（Colab版の賢い探索を継承） =====
def deep_find_dates(obj):
    """'user'配下を除外して日付を探索（Colab版ロジック）"""
    found = {}
    target_keys = {"published_at", "publish_at", "first_published_at", "created_at", "updated_at"}
    def walk(o, current_key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "user": continue # user配下は無視
                if k in target_keys and k not in found:
                    found[k] = v
                walk(v, k)
        elif isinstance(o, list):
            for v in o[:50]: walk(v)
    walk(obj)
    # 代表的なキーにマッピング
    return {
        "published_at": found.get("published_at") or found.get("publish_at") or found.get("first_published_at"),
        "created_at": found.get("created_at"),
        "updated_at": found.get("updated_at")
    }

def fetch_stats(session):
    all_notes = []
    page = 1
    total_data = {}
    while True:
        r = session.get(f"{BASE_URL}/api/v1/stats/pv", params={"filter": "all", "page": page, "sort": "pv"})
        r.raise_for_status()
        data = r.json().get("data", {})
        if page == 1:
            total_data = {k: data.get(k) for k in ["total_pv", "total_like", "total_comment"]}
        all_notes.extend(data.get("note_stats", []))
        if data.get("last_page"): break
        page += 1
    
    u = session.get(f"{BASE_URL}/api/v2/creators/{NOTE_USERNAME}")
    total_data["follower_count"] = u.json().get("data", {}).get("followerCount") if u.status_code == 200 else None
    return all_notes, total_data

# ===== 3. メイン処理・保存（スプシ形式準拠） =====
def main():
    validate_setup()
    session = make_session()
    verify_auth(session)
    
    print("\n📊 記事データ取得中...")
    notes, summary = fetch_stats(session)
    df = pd.DataFrame(notes)
    
    # カラム名をスプシ仕様に「擬態」させる
    df = df.rename(columns={"name": "title", "read_count": "view", "comment_count": "comment", "like_count": "like"})
    
    # キャッシュ読み込み
    cache = {}
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f: cache = json.load(f)

    print("\n📅 投稿日をv3 APIから補完中（キャッシュ活用）...")
    updated_cache = False
    for i, row in df.iterrows():
        key = row["key"]
        if key in cache:
            dates = cache[key]
        else:
            r = session.get(f"{BASE_URL}/api/v3/notes/{key}")
            if r.status_code == 200:
                dates = deep_find_dates(r.json().get("data", {}))
                cache[key] = dates
                updated_cache = True
                time.sleep(0.1) # 負荷軽減
            else:
                dates = {}

        for k, v in dates.items():
            df.at[i, k] = v

    if updated_cache:
        DATA_DIR.mkdir(exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f: json.dump(cache, f, ensure_ascii=False, indent=2)

    # 日付変換と経過日数計算
    now_jst = datetime.now(JST)
    for col in ["published_at", "created_at", "updated_at"]:
        df[col] = pd.to_datetime(df[col]).dt.tz_convert("Asia/Tokyo") if df[col].notna().any() else df[col]
    
    df["age_days"] = (now_jst - df["published_at"]).dt.days if "published_at" in df else ""

    # 🌟 スプシと全く同じ並び順で保存
    final_cols = ["key", "title", "published_at", "created_at", "updated_at", "age_days", "view", "comment", "like"]
    DATA_DIR.mkdir(exist_ok=True)
    df[final_cols].to_csv(DATA_DIR / "articles.csv", index=False, encoding="utf-8")
    
    # サマリー保存
    summary_path = DATA_DIR / "daily_summary.csv"
    summary_row = pd.DataFrame([{
        "date": now_jst.strftime("%Y-%m-%d"),
        "article_count": len(df),
        "total_pv": summary["total_pv"],
        "total_like": summary["total_like"],
        "total_comment": summary["total_comment"],
        "follower_count": summary["follower_count"]
    }])
    summary_row.to_csv(summary_path, mode='a', header=not summary_path.exists(), index=False)
    
    print(f"\n=== 完了: {len(df)}記事取得完了 ===")

if __name__ == "__main__":
    main()
