from PIL import Image

im = Image.open("output/images/YURANGGU_FINAL.jpg")
w, h = im.size
print("full size:", w, h)
# 상단부 여러 크롭 후보를 만들어서 눈으로 확인
for frac in [0.08, 0.10, 0.12]:
    top = int(h * frac)
    crop = im.crop((0, top, w, top + 200))
    crop.save(f"output/images/_probe_top_{int(frac*100)}.jpg", quality=90)
    print(frac, top)
