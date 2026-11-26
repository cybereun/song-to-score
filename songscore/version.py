"""앱 버전과 업데이트 확인 정보."""
import os

VERSION = "1.1.0"
REPO = "cybereun/song-to-score"
DEVELOPER = "@2026Lebi_Cybereun"
HOMEPAGE = "https://cybereun.github.io/song-to-score/"
REPO_URL = f"https://github.com/{REPO}"
THREADS = "https://www.threads.com/@lebi_cyber"

# 테스트용: 현재 버전을 다르게 보이게 하거나(업데이트 알림 확인), 확인 주소를 바꿀 수 있다
CURRENT = os.environ.get("SONGSCORE_VERSION_OVERRIDE", VERSION)
LATEST_URL = os.environ.get("SONGSCORE_UPDATE_URL", f"https://api.github.com/repos/{REPO}/releases/latest")


def parse(v: str) -> tuple:
    v = v.strip().lstrip("vV")
    out = []
    for part in v.split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))
