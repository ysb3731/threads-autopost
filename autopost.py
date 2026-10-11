"""
Threads 자동 게시 (쿠파스 시스템)
- mode: coupang  → 쿠팡 파트너스 API로 상품 선정 → Claude가 소개글 작성 → 이미지+글 게시 → 링크는 댓글로
- mode: content  → 일반 정보성 글 (계정 신뢰도·팔로워 확보용)
- 계정별로 coupang_ratio 비율만큼 쿠팡 글, 나머지는 정보성 글을 섞어 올린다

환경변수
  ANTHROPIC_API_KEY                 Claude API 키
  THREADS_USER_ID_N / THREADS_TOKEN_N   계정별 Threads ID·장기 토큰
  COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY  쿠팡 파트너스 API 키
  DRY_RUN=1      게시 없이 결과만 출력
  MOCK_LLM=1     Claude 호출 없이 테스트 문장
  MOCK_COUPANG=1 쿠팡 API 없이 가짜 상품
  FORCE=1        시간대·건너뛰기 무시
  ONLY_ACCOUNT=이름  특정 계정만
  FORCE_MODE=coupang|content  이번 실행의 글 종류 고정
"""
import datetime
import json
import os
import pathlib
import random
import sys
import time

import requests
import yaml

import coupang

KST = datetime.timezone(datetime.timedelta(hours=9))
ROOT = pathlib.Path(__file__).resolve().parent
API = "https://graph.threads.net/v1.0"
THREADS_MAX_CHARS = 500


def env_flag(name):
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------- 이력 ----------
def history_file(name):
    d = ROOT / "history"
    d.mkdir(exist_ok=True)
    return d / f"{name}.jsonl"


def load_history(name, n):
    path = history_file(name)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").strip().splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def save_history(name, record):
    with open(history_file(name), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------- Claude ----------
def ask_claude(cfg, prompt):
    if env_flag("MOCK_LLM"):
        stamp = datetime.datetime.now(KST).strftime("%H:%M:%S")
        return f"[테스트 글 {stamp}]\n{prompt.splitlines()[-3][:120]}"
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        raise RuntimeError("ANTHROPIC_API_KEY 미등록 → Secrets에 Claude API 키를 넣거나, 테스트는 mock_llm=1로 실행하세요")
    import anthropic

    msg = anthropic.Anthropic().messages.create(
        model=cfg.get("model", "claude-haiku-5-5"),
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


COMMON_RULES = """- 한국어, 본문 {body_max}자 이내
- 첫 줄은 스크롤을 멈추게 하는 짧은 한 문장
- 2~3줄마다 줄바꿈, 모바일에서 읽기 쉽게
- 해시태그 0~2개, 이모지 최대 2개
- 과장·허위 정보, 의학·효능 단정 표현 금지
- 아래 최근 글과 첫 문장·구성이 겹치지 않게"""


def content_prompt(acc, topic, recent, body_max):
    recent_block = "\n".join(f"- {t[:80]}" for t in recent) or "(없음)"
    return f"""Threads(스레드) 게시글 1개를 작성해줘.

[계정 콘셉트]
{acc.get('persona', '').strip()}

[이번 글 주제]
{topic}

[작성 규칙]
{COMMON_RULES.format(body_max=body_max)}
- 상품 홍보·링크 언급 금지 (순수 정보글)

[최근 게시글]
{recent_block}

설명 없이 게시글 본문만 출력해.
주제: {topic}"""


def clean_name(name):
    """판매자 광고 문구([...], (...))와 옵션 표기 제거"""
    import re
    name = re.sub(r"\[[^\]]*\]|【[^】]*】", "", name)
    return re.sub(r"\s+", " ", name.split(",")[0]).strip()


def coupang_prompt(acc, product, keyword, recent, body_max):
    recent_block = "\n".join(f"- {t[:80]}" for t in recent) or "(없음)"
    perks = []
    if product["rocket"]:
        perks.append("로켓배송")
    if product["free_shipping"]:
        perks.append("무료배송")
    price = f"{product['price']:,}원" if isinstance(product["price"], (int, float)) else "가격 변동"
    return f"""Threads(스레드)에 올릴 상품 소개글 1개를 작성해줘.

[계정 콘셉트]
{acc.get('persona', '').strip()}

[상품 정보 - 이 정보만 사용]
- 상품명: {clean_name(product['name'])}
- 검색 키워드: {keyword}
- 카테고리: {product['category']}
- 가격(조회 시점): {price}
- 배송: {', '.join(perks) or '정보 없음'}

[작성 규칙]
{COMMON_RULES.format(body_max=body_max)}
- 직접 써봤다는 표현·가짜 후기 금지. "이런 분께 유용해요" 식의 상황 제안형으로
- 상품명에 없는 성능·성분·효과를 지어내지 말 것
- 가격은 변동될 수 있으니 "현재 기준" 정도로만 언급하거나 생략
- "링크 클릭", "지금 구매" 같은 직접 구매 유도 문구 금지
- 계정 콘셉트 문장을 그대로 옮기지 말고 말투·관점에만 녹일 것 ("아이 둘 키우는 아빠로서" 같은 자기소개 금지)
- 판매자 광고 문구를 따옴표로 인용하지 말 것
- 글 마지막에 "댓글", "링크" 안내 문장은 쓰지 말 것 (시스템이 자동으로 붙임)
- 문장은 반드시 완결된 형태로 끝낼 것

[최근 게시글]
{recent_block}

설명 없이 게시글 본문만 출력해.
상품: {product['name'][:60]}"""


# ---------- 쿠팡 상품 선정 ----------
def short_link(product):
    """긴 추적 링크 → link.coupang.com/a/xxxx 단축 링크 (실패 시 원래 링크)"""
    url = product["url"]
    if "/a/" in url or not os.getenv("COUPANG_ACCESS_KEY"):
        return url
    try:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)
        page = q.get("pageKey", [product["id"]])[0]
        item = q.get("itemId", [""])[0]
        vendor = q.get("vendorItemId", [""])[0]
        raw = f"https://www.coupang.com/vp/products/{page}"
        if item and vendor:
            raw += f"?itemId={item}&vendorItemId={vendor}"
        short = coupang.deeplink([raw])[0]
        return short or url
    except Exception as e:
        print(f"  ! 단축 링크 변환 실패, 원래 링크 사용: {e}")
        return url


def pick_product(acc, history):
    cp = acc.get("coupang", {})
    posted = {h.get("product_id") for h in history if h.get("product_id")}

    # API 키가 없으면 config의 manual_products(파트너스 사이트에서 직접 만든 링크) 사용
    if not os.getenv("COUPANG_ACCESS_KEY") and not env_flag("MOCK_COUPANG"):
        manual = [
            {"id": m["url"], "name": m["name"], "price": m.get("price"), "image": m.get("image"),
             "url": m["url"], "rocket": False, "free_shipping": False, "category": m.get("category", "")}
            for m in cp.get("manual_products", []) if m.get("url")
        ]
        cands = [p for p in manual if p["id"] not in posted] or manual  # 다 쓰면 순환
        return (random.choice(cands), "수동 등록") if cands else (None, None)

    sources = []
    for kw in cp.get("keywords", []):
        sources.append(("search", kw))
    for cat in cp.get("best_categories", []):
        sources.append(("best", cat))
    if cp.get("use_goldbox"):
        sources.append(("goldbox", "goldbox"))
    random.shuffle(sources)

    max_price = cp.get("max_price")
    for source, value in sources[:3]:  # 호출 수 제한: 회차당 최대 3개 소스
        try:
            products = coupang.get_products(source, value, limit=cp.get("search_limit", 10),
                                            ttl_hours=cp.get("cache_hours", 12))
        except Exception as e:
            print(f"  ! 쿠팡 조회 실패({source}:{value}): {e}")
            continue
        cands = [p for p in products if p["id"] not in posted]
        if cp.get("rocket_only"):
            cands = [p for p in cands if p["rocket"]]
        if max_price:
            cands = [p for p in cands if isinstance(p["price"], (int, float)) and p["price"] <= max_price]
        if cands:
            top = cands[: cp.get("pick_from_top", 5)]
            return random.choice(top), (value if source != "best" else f"베스트({value})")
    return None, None


# ---------- Threads API ----------
def _create(user_id, token, data):
    r = requests.post(f"{API}/{user_id}/threads", data={**data, "access_token": token}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"컨테이너 생성 실패 {r.status_code}: {r.text}")
    return r.json()["id"]


def _wait_ready(container_id, token, tries=12):
    for _ in range(tries):
        r = requests.get(f"{API}/{container_id}", params={"fields": "status,error_message",
                                                          "access_token": token}, timeout=30)
        st = r.json().get("status") if r.status_code == 200 else None
        if st == "FINISHED":
            return
        if st == "ERROR":
            raise RuntimeError(f"미디어 처리 실패: {r.json().get('error_message')}")
        time.sleep(5)
    raise RuntimeError("미디어 처리 시간 초과")


def _publish(user_id, token, creation_id):
    r = requests.post(f"{API}/{user_id}/threads_publish",
                      data={"creation_id": creation_id, "access_token": token}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"게시 실패 {r.status_code}: {r.text}")
    return r.json()["id"]


def threads_post(user_id, token, text, image_url=None, reply_to=None):
    data = {"text": text}
    if image_url:
        data.update(media_type="IMAGE", image_url=image_url)
    else:
        data["media_type"] = "TEXT"
    if reply_to:
        data["reply_to_id"] = reply_to
    cid = _create(user_id, token, data)
    if image_url:
        _wait_ready(cid, token)
    else:
        time.sleep(5)
    return _publish(user_id, token, cid)


# ---------- 실행 ----------
def should_run_now(cfg):
    if env_flag("FORCE"):
        return True
    now = datetime.datetime.now(KST)
    if now.hour not in cfg.get("active_hours_kst", list(range(24))):
        print(f"활동 시간 아님 (KST {now.hour}시) → 건너뜀")
        return False
    if random.random() < cfg.get("skip_probability", 0):
        print("자연스러운 간격을 위해 이번 회차 건너뜀")
        return False
    return True


def choose_mode(acc):
    forced = os.getenv("FORCE_MODE", "").strip()
    if forced in ("coupang", "content"):
        return forced
    if not acc.get("coupang", {}).get("enabled"):
        return "content"
    return "coupang" if random.random() < acc["coupang"].get("ratio", 0.4) else "content"


def build_post(cfg, acc, history):
    """(mode, 본문, 이미지URL, 댓글, 메타) 반환"""
    recent = [h["text"] for h in history[-cfg.get("history_lookback", 20):]]
    body_max = acc.get("body_max_chars", cfg.get("body_max_chars", 300))
    mode = choose_mode(acc)

    if mode == "coupang":
        product, keyword = pick_product(acc, history)
        if product:
            text = ask_claude(cfg, coupang_prompt(acc, product, keyword, recent, body_max))
            tag = cfg.get("ad_tag", "#광고 #쿠팡파트너스")
            outro = acc["coupang"].get("outro", "자세한 정보는 댓글에 남겨둘게요 👇")
            tail = f"\n\n{outro}\n{tag}"
            text = f"{text[: THREADS_MAX_CHARS - len(tail)].rstrip()}{tail}"
            link = short_link(product)
            reply = f"{acc['coupang'].get('link_label', '👉 상품 보기')}\n{link}\n\n{cfg['disclosure']}"
            product = {**product, "url": link}
            image = product["image"] if acc["coupang"].get("use_image", True) else None
            meta = {"product_id": product["id"], "product_name": product["name"],
                    "keyword": keyword, "link": product["url"]}
            if len(reply) > THREADS_MAX_CHARS:  # 고지 문구가 잘리면 안 되므로 게시 포기
                raise RuntimeError(f"링크 댓글이 {len(reply)}자로 너무 김 (고지 문구 보호)")
            return mode, text, image, reply, meta
        print("  ! 조건에 맞는 새 상품 없음 → 정보성 글로 대체")
        mode = "content"

    topic = random.choice(acc["topics"])
    text = ask_claude(cfg, content_prompt(acc, topic, recent, body_max))[:THREADS_MAX_CHARS]
    return mode, text, None, None, {"topic": topic}


def run_account(cfg, acc, dry_run):
    name = acc["name"]
    print(f"\n=== [{name}] ===")
    user_id = os.getenv(acc["user_id_env"], "").strip()
    token = os.getenv(acc["token_env"], "").strip()
    if not dry_run and (not user_id or not token):
        print(f"  ! {acc['user_id_env']} / {acc['token_env']} 미설정 → 건너뜀")
        return False

    history = load_history(name, 200)
    mode, text, image, reply, meta = build_post(cfg, acc, history)
    if not text:
        print("  ! 생성된 글이 비어 있음 → 건너뜀")
        return False

    print(f"  종류: {mode}  {json.dumps(meta, ensure_ascii=False)[:150]}")
    print(f"  ---\n{text}\n  --- ({len(text)}자)")
    if image:
        print(f"  이미지: {image}")
    if reply:
        print(f"  [댓글]\n{reply}")

    if dry_run:
        print("  (DRY_RUN: 게시하지 않음)")
        return True

    post_id = threads_post(user_id, token, text, image_url=image)
    print(f"  게시 완료: {post_id}")
    reply_id = None
    if reply:
        time.sleep(random.randint(20, 60))
        reply_id = threads_post(user_id, token, reply, reply_to=post_id)
        print(f"  링크 댓글 완료: {reply_id}")

    save_history(name, {
        "time": datetime.datetime.now(KST).isoformat(timespec="seconds"),
        "mode": mode, "post_id": post_id, "reply_id": reply_id, "text": text, **meta,
    })
    return True


def main():
    cfg = load_config()
    dry_run = env_flag("DRY_RUN")
    if not should_run_now(cfg):
        return 0
    jitter = cfg.get("jitter_max_seconds", 0)
    if jitter and not env_flag("FORCE"):
        time.sleep(random.randint(0, jitter))

    only = os.getenv("ONLY_ACCOUNT", "").strip()
    accounts = [a for a in cfg["accounts"] if a.get("enabled", True)]
    if only:
        accounts = [a for a in accounts if a["name"] == only]

    failures = 0
    for i, acc in enumerate(accounts):
        if i > 0 and not dry_run:
            time.sleep(random.randint(10, 40))
        try:
            run_account(cfg, acc, dry_run)
        except Exception as e:
            failures += 1
            print(f"  ! 오류: {e}")
    return 1 if failures == len(accounts) and accounts else 0


if __name__ == "__main__":
    sys.exit(main())
