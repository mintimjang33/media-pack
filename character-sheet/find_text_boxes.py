from PIL import Image
import numpy as np

im = Image.open("output/images/YURANGGU_FINAL.jpg").convert("RGB")
arr = np.array(im)
h, w, _ = arr.shape
print("size:", w, h)

def dark_mask(region):
    # region: HxWx3
    gray = region.mean(axis=2)
    return gray < 120  # 검은 텍스트는 배경(약 217)보다 훨씬 어두움

# 1) 좌상단 제목 텍스트 영역 후보: x 0~500, y 0~100
region1 = arr[0:100, 0:500]
mask1 = dark_mask(region1)
ys, xs = np.where(mask1)
if len(xs) > 0:
    print("title text bbox: x", xs.min(), xs.max(), "y", ys.min(), ys.max())
else:
    print("title text: none found in probe region")

# 2) "토갓핫"/"보짐둠" 라벨 영역 후보: x 750~1050, y 0~350 (오른쪽 절반, 상단부)
region2 = arr[0:350, 750:1050]
mask2 = dark_mask(region2)
ys2, xs2 = np.where(mask2)
if len(xs2) > 0:
    print("side labels bbox (offset+750,+0): x", xs2.min()+750, xs2.max()+750, "y", ys2.min(), ys2.max())
else:
    print("side labels: none found in probe region")
