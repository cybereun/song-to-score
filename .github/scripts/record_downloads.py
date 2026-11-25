"""매일 한국 시각 자정에 릴리즈 설치 파일의 누적 다운로드 수를 기록한다.

홈페이지는 (지금 누적 수 - 오늘 0시 기록) 으로 '오늘 다운로드 수'를 계산한다.
기록 파일: docs/stats/downloads.json
"""
import datetime as dt
import json
import os
import urllib.request
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "cybereun/song-to-score")
OUT = Path("docs/stats/downloads.json")
KST = dt.timezone(dt.timedelta(hours=9))


def total_downloads() -> int:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases?per_page=100",
        headers={"Accept": "application/vnd.github+json",
                 **({"Authorization": f"Bearer {os.environ['GH_TOKEN']}"} if os.environ.get("GH_TOKEN") else {})},
    )
    releases = json.load(urllib.request.urlopen(req))
    return sum(a["download_count"] for r in releases for a in r.get("assets", []) if a["name"].endswith(".exe"))


def main():
    now = dt.datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"daily": {}}
    total = total_downloads()
    daily = data.setdefault("daily", {})
    if today not in daily:  # 그날 처음 기록한 값이 '그날 0시 기준'
        daily[today] = {"start_total": total, "recorded_at": now.isoformat(timespec="minutes")}
    data["latest_total"] = total
    data["updated_at"] = now.isoformat(timespec="minutes")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(today, "start_total", daily[today]["start_total"], "now", total)


if __name__ == "__main__":
    main()
