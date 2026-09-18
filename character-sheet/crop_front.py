from PIL import Image

im = Image.open("output/images/YURANGGU_FINAL.jpg")
w, h = im.size

# 앞모습(왼쪽 인물)만, 상단 제목/좌측 라벨 텍스트 제외, 하단 아이콘줄 제외
left = int(w * 0.02)
right = int(w * 0.47)
top = int(h * 0.14)
bottom = int(h * 0.90)

crop = im.crop((left, top, right, bottom))
crop.save("output/images/YURANGGU_FRONT_ONLY.jpg", quality=95)
print("cropped size:", crop.size)
