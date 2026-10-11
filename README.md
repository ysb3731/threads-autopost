# Threads 자동 게시 시스템

Claude가 글을 쓰고, GitHub Actions가 매시간 Threads에 올려줍니다. PC를 켜 둘 필요 없음.

```
GitHub Actions (매시 7분) → autopost.py → Claude API(글 생성) → Threads API(게시) → history/ 기록
GitHub Actions (월 2회)   → refresh_tokens.py → Threads 토큰 60일 연장
```

## 1회 설정 (약 30분)

### ① Threads API 토큰 발급
1. developers.facebook.com → 앱 만들기 → 사용 사례 **"Threads API 액세스"** 선택
2. 권한: `threads_basic`, `threads_content_publish` 추가
3. 앱 역할 → **Threads 테스터**에 게시할 계정 추가 → Threads 앱(설정 › 계정 › 웹사이트 권한)에서 초대 수락
4. Graph API 탐색기(또는 앱 대시보드)에서 사용자 토큰 발급 → **장기 토큰(60일)**으로 교환
5. `GET https://graph.threads.net/v1.0/me?fields=id,username&access_token=토큰` 으로 **user_id** 확인

> 계정 2개면 위 과정을 계정별로 반복합니다.

### ② Claude API 키
console.anthropic.com → API Keys → 키 생성 (결제수단 등록 필요)

### ③ GitHub 저장소
1. **비공개(Private)** 저장소 생성 → 이 폴더 전체 업로드
2. Settings → Secrets and variables → Actions → 아래 등록

| 이름 | 값 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `THREADS_USER_ID_1` / `THREADS_TOKEN_1` | 1번 계정 ID / 장기 토큰 |
| `THREADS_USER_ID_2` / `THREADS_TOKEN_2` | 2번 계정 (쓸 때만) |
| `GH_PAT` | 토큰 자동 연장용. GitHub → Settings → Developer settings → Fine-grained token, 이 저장소의 **Secrets: Read and write** 권한 |

3. Settings → Actions → General → Workflow permissions → **Read and write** 체크

### ④ 테스트
Actions 탭 → "Threads 자동 게시" → Run workflow
- `dry_run=1` : 글만 생성해서 로그로 확인 (게시 안 함)
- 문제없으면 `dry_run=0` 으로 1회 실제 게시 확인

이후로는 매시간 자동으로 돌아갑니다.

## 쿠파스(쿠팡 파트너스) 모드

```
키워드 → 쿠팡 API 상품 검색 → 이미 올린 상품 제외·로켓배송·가격 필터 → Claude 소개글
→ [본문 + 상품 이미지 + #광고 #쿠팡파트너스] 게시 → 20~60초 후 [링크 + 대가성 고지] 댓글
```
- 정보성 글과 `ratio` 비율로 섞어 올림 (기본 쿠팡 40%) → 광고 계정으로 찍히는 것 방지
- 같은 상품 재게시 방지, 키워드별 조회 결과 12시간 캐시 (API 호출 제한 대응)

**API 키 발급:** partners.coupang.com 로그인 → 추가 기능 → **Open API** → 키 발급
→ GitHub Secrets에 `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY` 등록
> Open API는 파트너스 최종 승인 + 일정 판매 실적이 있어야 열리는 경우가 많습니다. 파트너스 화면에서 발급 가능 여부를 먼저 확인하세요.

**API가 아직 없을 때:** `coupang.enabled: false`로 두고 정보성 글로 계정을 먼저 키우거나, 파트너스 사이트에서 만든 링크를 수동으로 넣는 방식으로 운영 가능.

## 운영 조절 (config.yaml만 수정)

| 항목 | 의미 |
|---|---|
| `active_hours_kst` | 게시할 시간대 (기본 7~23시) |
| `skip_probability` | 회차 건너뛸 확률 (0.15 → 하루 약 14~15개/계정) |
| `persona`, `topics` | 계정 콘셉트·주제 목록 |
| `links`, `link_probability` | 쿠팡 링크 목록과 첨부 확률 (고지 문구 자동 포함) |
| `enabled` | 계정 켜기/끄기 |
| `model` | `claude-haiku-5-5`(저렴) / `claude-sonnet-5-5`(고품질) |

**정지:** Actions 탭 → 워크플로 → `...` → Disable workflow

## 로컬 점검
```bash
pip install -r requirements.txt
MOCK_LLM=1 DRY_RUN=1 FORCE=1 python autopost.py   # API 없이 동작 확인
DRY_RUN=1 FORCE=1 python autopost.py              # 실제 Claude로 글만 생성
```

## 주의
- 쿠팡 링크엔 대가성 문구 필수 (자동 삽입됨, 삭제 금지)
- 너무 기계적인 패턴·유사 글 반복은 Threads 노출 제한 위험 → 처음엔 계정당 하루 5~10개 권장
- Threads API 게시 한도: 계정당 24시간 250개 (공식 문서 기준, 변경 가능)
- GitHub 예약 실행은 몇 분~수십 분 지연될 수 있음
