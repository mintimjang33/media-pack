"""공학 쇼츠 자막 — 한 줄 폭 계산 + 이음새 기준 쪼개기.

자막은 **항상 한 줄**이다(2026-08-20 사용자 지시). 줄 수가 컷마다 바뀌면 자막 덩어리의
세로 기준선이 출렁여 읽는 눈이 끊긴다. 한 줄에 안 들어가는 문장은 줄바꿈 대신
**시간으로 쪼개** 컷 안에서 순차 재생한다(EngShorts 의 SubSeq).

★`em()` 의 계수는 `remotion_app/src/engshorts/parts.tsx` 의 `subEm` 과 같아야 한다.
  한쪽만 바꾸면 한 줄이 넘친다.

규격 = .claude/skills/engineering-shorts/SKILL.md STEP 8
"""
from __future__ import annotations

from itertools import combinations, pairwise

PUNCT = ".,?!·~"

#: 자막 가용 폭(px) — 1080 폭에서 좌우 여백을 뺀 값. parts.tsx SUB_AVAIL 와 동일
SUB_AVAIL = 950.0
#: 한 줄 최대 폭(em). 950/21 = 45px — 이 아래로는 폰에서 흐리다
MAX_EM = 21.0

# ── 끊어도 되는 자리 / 끊으면 안 되는 자리 ──────────────────────────────
# 글자 수만 맞춰 반씩 자르면 `천 / 분의 일`·`되지 / 않냐고요`·`밀어 / 넣으니` 처럼
# 한 덩어리를 가른다(2026-08-20 실측). 그래서 후보 경계마다 점수를 매겨 고른다.
GOOD_END = ("고", "며", "면", "니", "데", "만", "서", "거든요", "죠", "요", "지만", "는데")
PARTICLE = ("은", "는", "이", "가", "을", "를", "에", "의", "로", "와", "과", "도", "에서", "으로")
#: 뒤 어절 없이는 뜻이 없는 앞 어절 — 여기서 끊으면 말이 공중에 뜬다
DANGLING = frozenset({
    "그", "이", "저", "한", "두", "세", "네", "몇", "천", "백", "만", "억",
    "각", "전", "약", "더", "안", "못",
})
#: 앞 어절과 한 덩어리로 붙는 뒤 어절
GLUE_NEXT = ("분의", "않", "못", "안", "없", "넣", "주고", "주는", "두고", "보는", "버리")


def em(t: str) -> float:
    """자막 폭(em) 추정 — 한글은 전각 1.0, 공백·라틴·문장부호는 좁다."""
    v = 0.0
    for ch in t:
        if ch == " ":
            v += 0.31
        elif ch.isascii() and ch.isalnum():
            v += 0.55
        elif ch in PUNCT:
            v += 0.42
        else:
            v += 1.0
    return v


def font_px(t: str, max_px: float = 54.0, min_px: float = 30.0) -> float:
    """한 줄에 맞춘 글자 크기(px). parts.tsx Subtitle 와 같은 식."""
    return max(min_px, min(max_px, SUB_AVAIL / em(t)))


def break_score(left: str, right: str) -> float:
    """경계 점수 — 낮을수록 좋은 자리."""
    lw = left.split()[-1]
    rw = right.split()[0]
    sc = 0.0
    if lw.endswith((",", "?", "!")):
        sc -= 8
    elif lw.endswith(GOOD_END):
        sc -= 3
    elif lw.endswith(PARTICLE):
        sc -= 1
    if lw in DANGLING:
        sc += 14                       # "천" 뒤에서 끊기
    if rw.startswith(GLUE_NEXT):
        sc += 14                       # "분의 일"·"않냐고요"·"넣으니" 앞에서 끊기
    if lw.endswith(("어", "아")) and len(lw) <= 3:
        sc += 8                        # 보조용언 연결("밀어 넣으니")
    return sc


def chunk(t: str, max_em: float = MAX_EM, max_parts: int = 4) -> list[str]:
    """자막을 한 줄씩 끊는다. 한 줄에 들어가면 그대로, 아니면 이음새 기준으로 쪼갠다.

    균형(폭 편차)보다 **말의 이음새**가 우선이다 — 어절 경계에서만 자르고,
    후보 조합을 전수 비교해 점수가 가장 낮은 것을 고른다.
    """
    if em(t) <= max_em:
        return [t]
    words = t.split()
    n = len(words)
    for k in range(2, max_parts + 1):
        if k > n:
            break
        best: list[str] | None = None
        best_sc = None
        for cuts_at in combinations(range(1, n), k - 1):
            bounds = (0, *cuts_at, n)
            parts = [" ".join(words[a:b]) for a, b in pairwise(bounds)]
            if any(em(x) > max_em for x in parts):
                continue
            ems = [em(x) for x in parts]
            sc = (max(ems) - min(ems)) * 0.7                    # 균형
            sc += sum(break_score(parts[i], parts[i + 1]) for i in range(k - 1))
            if best_sc is None or sc < best_sc:
                best, best_sc = parts, sc
        if best:
            return best
    return [t]                          # 어절 하나가 한 줄을 넘는 경우 — 그대로 두고 축소에 맡긴다
