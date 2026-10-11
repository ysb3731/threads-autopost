"""
Threads 장기 토큰(60일) 자동 연장 → GitHub Secrets 갱신
GitHub Actions에서 GH_PAT(Secrets 쓰기 권한 토큰)와 함께 실행됨.
토큰 값은 로그에 출력하지 않는다.
"""
import os
import pathlib
import subprocess
import sys

import requests
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    cfg = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
    repo = os.environ["GITHUB_REPOSITORY"]
    failed = 0
    for acc in cfg["accounts"]:
        if not acc.get("enabled", True):
            continue
        name, env = acc["name"], acc["token_env"]
        token = os.getenv(env, "").strip()
        if not token:
            print(f"[{name}] {env} 없음 → 건너뜀")
            continue
        r = requests.get(
            "https://graph.threads.net/refresh_access_token",
            params={"grant_type": "th_refresh_token", "access_token": token},
            timeout=30,
        )
        if r.status_code != 200:
            failed += 1
            print(f"[{name}] 연장 실패 {r.status_code}: {r.text}")
            continue
        new_token = r.json()["access_token"]
        days = r.json().get("expires_in", 0) // 86400
        subprocess.run(
            ["gh", "secret", "set", env, "--repo", repo, "--body", new_token],
            check=True,
        )
        print(f"[{name}] 토큰 연장 완료 (약 {days}일 유효)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
