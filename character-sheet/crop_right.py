from PIL import Image

im = Image.open("output/images/YURANGGU_FINAL.jpg")
w, h = im.size
print("size:", w, h)

# 대략적인 추정 크롭: 상단 텍스트/좌측 인물 제외, 우측(뒷모습) 인물만, 하단 아이콘줄 제외
left = int(w * 0.71)
right = int(w * 0.99)
top = int(h * 0.06)
bottom = int(h * 0.95)

crop = im.crop((left, top, right, bottom))
crop.save("output/images/YURANGGU_RIGHT_ONLY.jpg", quality=95)
print("cropped size:", crop.size)
