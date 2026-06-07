"""
Kho mẫu thành phẩm dùng làm ảnh tham chiếu phong cách cho AI.

- Lưu ảnh vào MEDIA_ROOT/style_samples/.
- Mỗi mẫu có 'sig' = chữ ký 8x8 RGB (giống ảnh thu nhỏ) để chọn nhanh các mẫu
  GIỐNG ảnh khách nhất, rồi gửi 3–4 mẫu đó kèm cho Gemini.
- KHÔNG huấn luyện model; chỉ làm few-shot bằng ảnh tham chiếu (Google khuyến nghị,
  tối đa 3–4 ảnh/lần) — rẻ, dùng ngay với GOOGLE_API_KEY.
"""
import os
import time
from datetime import datetime

from django.conf import settings

from pha.models import StyleSample

SAMPLES_SUBDIR = 'style_samples'
SIG_SIDE = 8  # ảnh thu nhỏ 8x8 -> 192 số (R,G,B)


def samples_dir():
    d = os.path.join(settings.MEDIA_ROOT, SAMPLES_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def signature(pil_image):
    """Chữ ký 8x8 RGB (list 192 số 0-255): nắm bố cục + tông màu tổng thể."""
    from PIL import Image
    img = pil_image.convert('RGB').resize((SIG_SIDE, SIG_SIDE), Image.BILINEAR)
    return [c for px in img.getdata() for c in px]


def _sig_distance(a, b):
    if not a or not b or len(a) != len(b):
        return float('inf')
    return sum((x - y) * (x - y) for x, y in zip(a, b))


def add_sample(django_file, category='', user=''):
    """Lưu 1 ảnh mẫu (UploadedFile của Django) + tính chữ ký. Trả về StyleSample."""
    from PIL import Image
    base = f'{datetime.now():%Y-%m-%d_%H-%M-%S}_{int(time.time()*1000)%100000}_{django_file.name}'
    rel = f'{SAMPLES_SUBDIR}/{base}'
    abspath = os.path.join(samples_dir(), base)
    with open(abspath, 'wb') as f:
        for chunk in django_file.chunks():
            f.write(chunk)
    try:
        sig = signature(Image.open(abspath))
    except Exception:
        sig = []
    return StyleSample.objects.create(name=rel, category=(category or '').strip(),
                                      sig=sig, user=user)


def categories():
    """Danh sách nhãn đang có (không rỗng), kèm số lượng."""
    from django.db.models import Count
    rows = (StyleSample.objects.exclude(category='')
            .values('category').annotate(n=Count('id')).order_by('category'))
    return [(r['category'], r['n']) for r in rows]


def pick_references(input_path, category=None, n=3):
    """Chọn tối đa n mẫu GIỐNG ảnh input nhất (lọc theo nhãn nếu có).
    Trả về danh sách đường dẫn tuyệt đối tới file ảnh mẫu."""
    from PIL import Image
    qs = StyleSample.objects.all()
    if category:
        qs = qs.filter(category=category)
    samples = list(qs.only('id', 'name', 'sig'))
    if not samples:
        return []
    try:
        target = signature(Image.open(input_path))
    except Exception:
        target = None

    if target:
        samples.sort(key=lambda s: _sig_distance(target, s.sig))

    out = []
    for s in samples[: n * 2]:  # dự phòng vài file đã mất
        p = os.path.join(settings.MEDIA_ROOT, s.name)
        if os.path.exists(p):
            out.append(p)
        if len(out) >= n:
            break
    return out
