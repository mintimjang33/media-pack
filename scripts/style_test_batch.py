# -*- coding: utf-8 -*-
"""7개 화풍 후보를 같은 씬(약사 스틱맨+무너지는 네온사인)으로 한 장씩 생성해서 비교하는 1회성 스크립트.
flow_econ_driver.py의 EconFlow 클래스를 그대로 재사용한다. 기존 경제학 채널 Flow 프로젝트를 재사용.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp_harness import CDP  # noqa: E402
from flow_econ_driver import EconFlow  # noqa: E402

PROJECT_URL = "https://flow.google.com/project/6f8960c1-1b98-4e73-b5c0-0e63010bf835"
OUT_DIR = Path(r"C:\Users\user\Downloads\화풍테스트")

BASE_SCENE = (
    "A stylized 2D stickman character with a simple round white face, dot eyes, "
    "and a white pharmacist coat. He is screaming in panic. Above him, a red neon "
    "sign reading 'PHARMACY' is crashing down onto a vintage wooden desk in a "
    "cluttered pharmacy interior with shelves of bottles."
)

STYLES = [
    ("01_2D일러스트", "Clean 2D digital illustration, crisp vector-style outlines, flat cel-shaded colors with subtle soft shading, balanced color palette, polished professional illustration look, no 3D, no photorealism."),
    ("02_연필그림", "Hand-drawn pencil sketch illustration, visible graphite pencil strokes and cross-hatching for shading, monochrome grayscale tones, textured sketchbook paper background, no color, no 3D, no photorealism."),
    ("03_수채화", "Watercolor painting illustration, soft translucent color washes, visible paper texture and gentle color bleeding at edges, delicate hand-painted look, muted pastel color palette, no 3D, no photorealism."),
    ("04_한국형웹툰", "Modern Korean webtoon illustration style, clean crisp digital linework, vivid flat-to-soft-gradient coloring, polished contemporary webtoon aesthetic typical of Korean comic platforms, no 3D, no photorealism."),
    ("05_손그림", "Casual hand-drawn doodle illustration, loose imperfect ink linework, marker-style flat coloring, playful sketchbook doodle aesthetic, no 3D, no photorealism."),
    ("06_수묵화", "Traditional East Asian ink wash painting (sumukhwa) style, monochrome black ink brush strokes with varying ink density, visible brush texture on traditional paper, minimal color, mostly black and white with subtle ink gray tones, no 3D, no photorealism."),
    ("07_기본(디테일웹툰)", "High-quality 2D digital illustration, bold webtoon/comic art style, clean thick black outlines, dynamic cel shading and dramatic lighting, rich detailed background matching the scene's environment, full expressive color palette, glowing/spark/lighting effects allowed for dramatic emphasis."),
]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    c = CDP("http://127.0.0.1:9223")
    f = EconFlow(c, shots=OUT_DIR / "_shots")
    f.open_project(PROJECT_URL)
    time.sleep(2)

    results = []
    for tag, style_text in STYLES:
        prompt = f"{style_text} {BASE_SCENE}"
        print(f"=== {tag} 제출 ===", flush=True)
        before = f.media_count()
        f.composer_click()
        f.type_text(prompt)
        f.submit()
        ok = f.wait_done(before, timeout=180, tag=tag)
        if not ok:
            print(f"  ✗ {tag} 타임아웃/실패", flush=True)
            results.append((tag, None))
            continue
        saved = f.capture_newest_images(OUT_DIR, tag, n=1)
        print(f"  ✓ {tag} 저장: {saved}", flush=True)
        results.append((tag, saved[0] if saved else None))
        time.sleep(2)

    print("\n=== 결과 요약 ===")
    for tag, path in results:
        print(tag, "->", path or "실패")


if __name__ == "__main__":
    main()
