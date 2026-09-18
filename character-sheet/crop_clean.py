from PIL import Image, ImageDraw

im = Image.open("output/images/YURANGGU_FINAL.jpg")
w, h = im.size

top = int(h * 0.16)
bottom = int(h * 0.90)
crop = im.crop((0, top, w, bottom)).convert("RGB")

# 배경색 샘플링 (좌측 빈 공간)
bg = crop.getpixel((20, 20))
draw = ImageDraw.Draw(crop)

# "보짐둠" 라벨 + 리더선 위치를 배경색으로 덮기 (crop 좌표계 기준, 넉넉히)
draw.rectangle([850, 130, 1000, 190], fill=bg)
# 상단 잔여 제목 텍스트 잘림 부분 마스킹
draw.rectangle([0, 0, 450, 30], fill=bg)

crop.save("output/images/YURANGGU_CLEAN.jpg", quality=95)
print("size:", crop.size, "bg:", bg)
