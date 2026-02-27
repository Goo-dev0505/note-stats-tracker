import os
import requests
import csv
import datetime

# GitHub Secretsから自動で読み込まれる設定
COOKIE = os.getenv("NOTE_COOKIE")
USERNAME = os.getenv("NOTE_USERNAME")

def fetch_note_stats():
    # Noteの公開プロフィールAPIからデータを取得
    url = f"https://note.com/api/v2/creators/{USERNAME}"
    headers = {"Cookie": COOKIE} if COOKIE else {}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()["data"]

def update_summary(data):
    # 日本時間(JST)でタイムスタンプを作成
    jst = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(jst)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # dataフォルダがなければ作成
    os.makedirs("data", exist_ok=True)
    file_path = "data/daily_summary.csv"
    file_exists = os.path.isfile(file_path)
    
    # データをCSVに追記
    with open(file_path, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            # 最初の1回だけヘッダーを書く
            writer.writerow(["timestamp", "follower_count", "note_count"])
        writer.writerow([timestamp, data["follower_count"], data["note_count"]])

if __name__ == "__main__":
    try:
        stats = fetch_note_stats()
        update_summary(stats)
        print(f"Successfully updated stats at {datetime.datetime.now()}")
    except Exception as e:
        print(f"Error occurred: {e}")
        exit(1)
