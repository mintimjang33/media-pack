# -*- coding: utf-8 -*-
"""코카콜라 유닛 씬 재설계 — 확정된 채널 캐릭터 시스템 적용판.
진행자(젠틀맨 루즈) + 의인화된 코카콜라 병 + 손 클로즈업, 3종 조합.
"""
import json

STYLE = ("Korean webtoon style illustration, clean vector line art, flat colors, "
         "dynamic and lively energy, minimalist and expressive.")

HOST = ("THE GENTLEMAN ROUGE: a stick figure character with a round pale cream-white face, "
        "a neat curled black mustache, a round gold-rimmed monocle over one eye held by a thin "
        "gold chain, a black top hat, a black tailcoat with white gloves, and a long black cape "
        "lined with deep gold draped over the shoulders, often holding a cane.")

BOTTLE = ("an anthropomorphized old-fashioned amber glass soda bottle character with simple "
          "stick arms and legs, a cork stopper on top like a hat, two simple round dot eyes and "
          "a small curved line mouth printed on the front where a label would be.")


def scene(id_, section, kind, desc, prompt):
    return {"id": id_, "section": section, "kind": kind, "desc": desc, "prompt": prompt}


SCENES = [
    scene("S01_COLDOPEN", "콜드오픈", "hand",
          "손 클로즈업 — 서류 서명, $300",
          "Close-up of a weathered hand holding an antique fountain pen, signing a yellowed contract on a worn wooden desk. The number \"$300\" is written large at the bottom of the page. Warm sepia-toned light through a window, dust particles in the light beam, cinematic composition, no face visible."),

    scene("S02_OPENING", "오프닝", "host",
          "젠틀맨 루즈가 시청자에게 직접 말을 거는 오프닝",
          f"{HOST} standing confidently, one hand gesturing outward toward the viewer as if addressing them directly, cane in the other hand. Plain simple background with a glowing rising stock chart graphic behind him. Chin lifted, sly confident smile."),

    scene("S03_ANDES", "1장", "object",
          "안데스 산맥, 코카 잎 (캐릭터 없음)",
          "A stylized illustration of the Andes mountain range at dusk, a cluster of green coca leaves in the foreground, warm earthy green and brown gradient, no characters, simple flat vector illustration."),

    scene("S04_CHEMLAB", "1장", "hand",
          "손 클로즈업 — 화학자가 결정 가루를 분리",
          "Close-up of a hand holding a small glass vial of white crystalline powder over a 19th century laboratory workbench, glass beakers around it, a handwritten label beside it, candlelight, no face visible."),

    scene("S05_SURGERY", "1장", "object",
          "의료 상징물 (캐릭터 없음)",
          "A simple flat vector icon illustration of an antique glass dropper bottle beside a stylized eye symbol, warm golden glow, vintage medical illustration style, no characters."),

    scene("S06_BIRTH", "2장", "hand",
          "손 클로즈업 — 펨버턴이 재료를 섞음(병 탄생 직전)",
          "Close-up of a pharmacist's hands mixing ingredients into a plain glass bottle on an old wooden apothecary counter lined with other bottles, warm afternoon light, no face visible."),

    scene("S07_BOTTLEBORN", "2장", "bottle",
          "의인화된 코카콜라 병 탄생 — 첫 등장",
          f"{BOTTLE} The bottle looks small, plain, and a little unsure of itself, standing shyly on a wooden pharmacy counter next to a few coins, warm sepia tone background, simple humble pose."),

    scene("S08_CONTRACT300", "2장", "hand",
          "손 클로즈업 — $300 계약 (콜드오픈 회수)",
          "Close-up of a hand sliding a signed contract across a wooden desk, the number \"$300\" visible at the bottom, matching the cold open composition, warm sepia lighting, no face visible."),

    scene("S09_CANDLER", "3장", "host",
          "젠틀맨 루즈가 사업가 캔들러의 등장을 설명",
          f"{HOST} standing beside a glowing rising bar chart, one hand pointing at it with interest, cane tucked under the other arm, plain simple background."),

    scene("S10_BOTTLEGROW", "3장", "bottle",
          "병 캐릭터가 성장 — 자신감 붙은 자세",
          f"{BOTTLE} The bottle now stands taller and more confidently, one arm raised in a small triumphant gesture, a simple elegant cursive-style flourish label on its front, warm golden light."),

    scene("S11_BOTTLECONFUSE", "4장", "bottle",
          "비슷한 병들 사이에서 혼란",
          f"{BOTTLE} standing among several other plain bottle shapes that look nearly identical, a confused expression, small question marks floating above, dim tavern background tone."),

    scene("S12_BOTTLECURVE", "4장", "bottle",
          "곡선 병으로 변신 — 자신감 있는 포즈",
          f"{BOTTLE} now redesigned with a distinctive curvy hourglass-shaped body, standing proudly with both hands on hip, a spotlight on it in darkness, confident glowing pose."),

    scene("S13_SANTA", "4장", "object",
          "산타 아이콘 (캐릭터 없음)",
          "A simple flat vector illustration of a classic Santa Claus figure in a red suit holding an amber glass bottle, warm winter red and white color palette, vintage advertisement poster style, no other characters."),

    scene("S14_BLINDTEST", "5장", "bottle",
          "병 캐릭터, 블라인드 테스트에 불안해함",
          f"{BOTTLE} looking nervous, one hand touching its own cork-stopper head, watching two unmarked cups on a table beside it, dim blue TV-lit background."),

    scene("S15_PROTEST", "5장", "object",
          "항의 편지·시위 팻말 더미 (캐릭터 없음)",
          "A pile of angry handwritten letters and simple protest signs scattered on a street, dark red alert color tones, no characters, flat vector illustration."),

    scene("S16_RELIEF", "5장", "bottle",
          "병 캐릭터, 안도하며 복귀",
          f"{BOTTLE} smiling with relief, wiping its cork-stopper forehead with one hand, standing back on a store shelf, warm golden light."),

    scene("S17_RIVALRY", "6장", "bottle",
          "코카콜라 병 vs 펩시 캔, 라이벌 구도",
          f"{BOTTLE} standing arms crossed facing off against a similarly cartoonish anthropomorphized soda can character with a bold red and blue label, small lightning bolt accents between them, dramatic split red-blue lighting."),

    scene("S18_BANKRUPT", "6장", "object",
          "파산 서류 더미 (캐릭터 없음)",
          "A stack of old legal documents stamped \"REJECTED\" in red twice, dim navy financial-crisis color tones, no characters, flat vector illustration."),

    scene("S19_GLOBALSCALE", "7장", "bottle",
          "병 캐릭터, 세계 지도 앞에서 환호",
          f"{BOTTLE} with both arms raised high in celebration, standing in front of a glowing world map covered in small light points, golden triumphant color palette."),

    scene("S20_TICKER", "7장", "host",
          "젠틀맨 루즈가 시가총액 숫자를 소개",
          f"{HOST} gesturing toward a large glowing stock ticker graphic showing rising numbers reaching \"$380B\", confident pointing pose, plain background."),

    scene("S21_FANTA", "8장", "bottle",
          "환타 병(사촌 캐릭터) 탄생",
          "An anthropomorphized green glass soda bottle character, similar simple stick-figure design with dot eyes and a curved line mouth, looking freshly made on a wartime factory production line, industrial muted orange and grey tones."),

    scene("S22_SPACE", "8장", "bottle",
          "병 캐릭터, 우주 비행",
          f"{BOTTLE} wearing a small round astronaut helmet, floating happily among stars with a spacecraft window in the background, deep space navy color palette."),

    scene("S23_COCALEAF", "8장", "object",
          "코카 잎 수입 서류 (캐릭터 없음)",
          "A simple flat vector illustration of an official import stamp on a legal document beside a small coca leaf icon, neutral warm beige tones, no characters."),

    scene("S24_INVESTOR", "8장", "host",
          "젠틀맨 루즈가 대주주 이야기를 소개",
          f"{HOST} standing beside a rising stock chart display, one hand on the chart, informative confident pose, plain background."),

    scene("S25_CLOSING", "클로징", "host",
          "젠틀맨 루즈, 망토를 젖히며 마무리(시그니처 리빌)",
          f"{HOST} in a dramatic pose flipping open the cape with one arm, the gold lining flashing brightly, the small anthropomorphized coke bottle character standing proudly beside him, warm triumphant golden lighting, confident closing pose."),
]

job = {"ratio": "16:9", "video_ratio": "16:9", "style": STYLE, "images": SCENES}

with open("flow_v2.json", "w", encoding="utf-8") as f:
    json.dump(job, f, ensure_ascii=False, indent=1)

print("OK, scenes:", len(SCENES))
kinds = {}
for s in SCENES:
    kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
print(kinds)
