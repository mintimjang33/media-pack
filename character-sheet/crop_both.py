from PIL import Image

im = Image.open("output/images/YURANGGU_FINAL.jpg")
w, h = im.size
print("size:", w, h)

# 상단 제목 텍스트만 제거, 좌우 앞/뒷모습은 전부 유지, 하단 반짝이 아이콘 여백만 살짝 제외
left = 0
right = w
top = int(h * 0.22)
bottom = int(h * 0.97)

crop = im.crop((left, top, right, bottom))
crop.save("output/images/YURANGGU_BOTH_VIEWS.jpg", quality=95)
print("cropped size:", crop.size)
