# -*- coding: utf-8 -*-
"""화풍 후보 2차 배치(8~13번) — 일본만화/디즈니/지브리/아메리칸코믹/픽셀아트/클레이.
같은 씬(약사 스틱맨+무너지는 네온사인)으로 한 장씩 생성해서 기존 7개와 비교한다.
flow_econ_driver.py의 EconFlow 클래스를 그대로 재사용, 기존 경제학 채널 Flow 프로젝트를 재사용.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp_harness import CDP  # noqa: E402
from flow_econ_driver import EconFlow  # noqa: E402

PROJECT_URL = "https://flow.google.com/project/6f8960c1-1b98-4e73-b5c0-0e63010bf835"
OUT_DIR = Path(r"C:\Users\user\Downloads\화풍테스트2")

BASE_SCENE = (
    "A stylized 2D stickman character with a simple round white face, dot eyes, "
    "and a white pharmacist coat. He is screaming in panic. Above him, a red neon "
    "sign reading 'PHARMACY' is crashing down onto a vintage wooden desk in a "
    "cluttered pharmacy interior with shelves of bottles."
)

STYLES = [
    ("08_일본만화", "Japanese anime illustration style, large expressive sparkling eyes with detailed highlights, sharp clean cel-shaded coloring, dynamic speed lines and screentone (halftone dot) shading for dramatic effect, glossy hair with defined light reflections, no 3D, no photorealism."),
    ("09_디즈니픽사", "Disney/Pixar-style 2D cartoon illustration, soft rounded shapes and exaggerated bouncy proportions, large round expressive eyes, smooth clean shading with warm rim lighting, vibrant saturated storybook color palette, polished family-animation look, no 3D render, no photorealism."),
    ("10_지브리", "Studio Ghibli-inspired illustration, soft painterly watercolor textures, gentle hand-painted brush strokes visible in the background, warm nostalgic natural lighting, muted earthy color palette with soft pastel accents, whimsical storybook atmosphere, no 3D, no photorealism."),
    ("11_아메리칸코믹", "American superhero comic book illustration style, bold thick black ink outlines, dramatic high-contrast cel shading, visible halftone dot printing texture, punchy primary color palette (red, blue, yellow), dynamic action-comic linework, no 3D, no photorealism."),
    ("12_레트로픽셀", "Retro 16-bit pixel art illustration, visible square pixel blocks, limited retro color palette, blocky simplified character shapes with no smooth curves, flat dithered shading reminiscent of classic video games, no 3D, no photorealism, no smooth vector lines."),
    ("13_클레이", "Claymation stop-motion illustration style, soft matte clay-like textures with visible fingerprint and tool-mark imperfections, chunky rounded character forms, warm diffused studio lighting, slightly imperfect handmade look, muted craft-material color palette, no photorealism, no glossy 3D render."),
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
            print(f"  X {tag} 타임아웃/실패", flush=True)
            results.append((tag, None))
            continue
        saved = f.capture_newest_images(OUT_DIR, tag, n=1)
        print(f"  OK {tag} 저장: {saved}", flush=True)
        results.append((tag, saved[0] if saved else None))
        time.sleep(2)

    print("\n=== 결과 요약 ===")
    for tag, path in results:
        print(tag, "->", path or "실패")


if __name__ == "__main__":
    main()
