from PIL import Image, ImageDraw

im = Image.open("output/images/YURANGGU_FINAL.jpg").convert("RGB")
w, h = im.size

# 모자 끝까지 다 보이게 상단을 거의 안 자르고, 대신 좌상단 제목 텍스트와 "보짐둠" 라벨만
# 배경색 사각형으로 덮어서 지운다.
top = 0
bottom = int(h * 0.90)
crop = im.crop((0, top, w, bottom))

bg = crop.getpixel((20, 200))
draw = ImageDraw.Draw(crop)
# 좌상단 제목 두 줄 텍스트 영역
draw.rectangle([0, 0, 460, 60], fill=bg)
# "보짐둠" 라벨 (위에서 top=0 크롭 기준으로 y 오프셋 재계산 필요)
draw.rectangle([850, 175, 1000, 230], fill=bg)

crop.save("output/images/YURANGGU_CLEAN2.jpg", quality=95)
print("size:", crop.size, "bg:", bg)
