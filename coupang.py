"""
쿠팡 파트너스 Open API 클라이언트
- HMAC 서명, 상품 검색, 골드박스, 카테고리 베스트, 딥링크 변환
- 결과는 cache/ 에 저장해 API 호출 수를 줄인다 (검색 API는 시간당 호출 제한이 있음)

환경변수
  COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY : 파트너스 API 키
  COUPANG_SUB_ID   : (선택) 채널 구분용 subId. 예) threads1
  MOCK_COUPANG=1   : API 없이 가짜 상품으로 테스트
"""
import datetime
import hashlib
import hmac
import json
import os
import pathlib
import time
import urllib.parse

import requests

DOMAIN = "https://api-gateway.coupang.com"
BASE = "/v2/providers/affiliate_open_api/apis/openapi/v1"
ROOT = pathlib.Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"


def _flag(name):
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def _auth(method, path, query):
    ak = os.environ["COUPANG_ACCESS_KEY"].strip()
    sk = os.environ["COUPANG_SECRET_KEY"].strip()
    signed = time.strftime("%y%m%d", time.gmtime()) + "T" + time.strftime("%H%M%S", time.gmtime()) + "Z"
    msg = signed + method + path + query
    sig = hmac.new(sk.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={ak}, signed-date={signed}, signature={sig}"


def _call(method, path, params=None, body=None):
    query = urllib.parse.urlencode(params or {})
    url = DOMAIN + path + (f"?{query}" if query else "")
    headers = {"Authorization": _auth(method, path, query), "Content-Type": "application/json;charset=UTF-8"}
    r = requests.request(method, url, headers=headers, data=json.dumps(body) if body else None, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"쿠팡 API 오류 {r.status_code}: {r.text[:300]}")
    j = r.json()
    if str(j.get("rCode", "0")) not in ("0", ""):
        raise RuntimeError(f"쿠팡 API 응답 오류: {j.get('rMessage')}")
    return j.get("data")


def _normalize(p):
    return {
        "id": str(p.get("productId")),
        "name": p.get("productName", "").strip(),
        "price": p.get("productPrice"),
        "image": p.get("productImage"),
        "url": p.get("productUrl"),  # 파트너스 추적 링크
        "rocket": bool(p.get("isRocket")),
        "free_shipping": bool(p.get("isFreeShipping")),
        "category": p.get("categoryName", ""),
    }


# ---------- 캐시 ----------
def _cache_get(key, ttl_hours):
    f = CACHE_DIR / f"{hashlib.md5(key.encode()).hexdigest()}.json"
    if f.exists() and time.time() - f.stat().st_mtime < ttl_hours * 3600:
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def _cache_put(key, data):
    CACHE_DIR.mkdir(exist_ok=True)
    f = CACHE_DIR / f"{hashlib.md5(key.encode()).hexdigest()}.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------- 공개 함수 ----------
def _mock(source, value):
    return [
        {
            "id": f"mock{i}",
            "name": f"[테스트] {value} 상품 {i}",
            "price": 12900 + i * 1000,
            "image": "https://via.placeholder.com/600.jpg",
            "url": f"https://link.coupang.com/a/mock{i}",
            "rocket": i % 2 == 0,
            "free_shipping": True,
            "category": "테스트",
        }
        for i in range(1, 6)
    ]


def get_products(source, value=None, limit=10, ttl_hours=12):
    """
    source: search(키워드 검색) | goldbox(오늘의 특가) | best(카테고리 베스트, value=카테고리ID)
    """
    if _flag("MOCK_COUPANG"):
        return _mock(source, value)

    key = f"{source}:{value}:{limit}"
    cached = _cache_get(key, ttl_hours)
    if cached is not None:
        return cached

    sub = os.getenv("COUPANG_SUB_ID", "").strip()
    extra = {"subId": sub} if sub else {}
    if source == "search":
        data = _call("GET", f"{BASE}/products/search", {"keyword": value, "limit": limit, **extra})
        items = (data or {}).get("productData", [])
    elif source == "goldbox":
        items = _call("GET", f"{BASE}/products/goldbox", extra) or []
    elif source == "best":
        items = _call("GET", f"{BASE}/products/bestcategories/{value}", {"limit": limit, **extra}) or []
    else:
        raise ValueError(f"알 수 없는 source: {source}")

    products = [_normalize(p) for p in items if p.get("productUrl")]
    _cache_put(key, products)
    return products


def deeplink(urls):
    """일반 쿠팡 상품 URL → 파트너스 단축 링크"""
    if _flag("MOCK_COUPANG"):
        return [f"https://link.coupang.com/a/mockdeep{i}" for i, _ in enumerate(urls)]
    sub = os.getenv("COUPANG_SUB_ID", "").strip()
    body = {"coupangUrls": urls, **({"subId": sub} if sub else {})}
    data = _call("POST", f"{BASE}/deeplink", body=body) or []
    return [d.get("shortenUrl") for d in data]


if __name__ == "__main__":  # 간단 점검: python coupang.py 키워드
    import sys

    kw = sys.argv[1] if len(sys.argv) > 1 else "물티슈"
    for p in get_products("search", kw, limit=5, ttl_hours=0):
        print(p["price"], p["name"][:40], p["url"])
