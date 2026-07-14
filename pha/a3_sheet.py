# -*- coding: utf-8 -*-
"""BƯỚC TẮT: bản đồ số -> thẳng PDF A3 IN SẴN (giống thợ làm tay trong Illustrator).

Dùng đúng template "A3 gốc" của xưởng (pha/assets/a3_template.pdf — trang 290×400mm
+ footer "CHÚ Ý" vector cố định): đặt bản đồ số vào ô giữa (giữ tỉ lệ, không méo) +
in MÃ tranh góc phải-dưới. Thay bước mở Illustrator thả ảnh + gõ mã + xuất tay.

Số đo lấy từ file mẫu C096 A3.pdf (thợ làm): ô ảnh (8.1,5.3)-(812.7,963.4)pt,
mã ở ~(260,339)mm 24pt.
"""
import os

from django.conf import settings
from django.http import JsonResponse

from pha.views import staff_required
from pha.imposition import _import_fitz

_TEMPLATE = os.path.join(os.path.dirname(__file__), 'assets', 'a3_template.pdf')

# Ô đặt bản đồ số trên trang (points, gốc trên-trái) — đo từ file thợ làm.
_BOX = (8.1, 5.3, 812.7, 963.4)
_CODE_SIZE = 24                 # cỡ mã (pt) như mẫu
_CODE_RIGHT = 792.0            # mép phải mã (pt) ~ mép phải ảnh
_CODE_BASELINE = 980.0        # baseline mã (pt) ~ ngay dưới ảnh, trên footer


def make_a3_sheet(number_map_path, code, out_path):
    """Ghép bản đồ số + mã lên template A3 -> lưu PDF. Trả out_path.
    number_map_path: ảnh bản đồ số (đường dẫn tuyệt đối). code: mã tranh (in lên)."""
    fitz = _import_fitz()
    if fitz is None:
        raise RuntimeError('Chưa cài PyMuPDF (pip install pymupdf) — không xuất được A3.')
    if not os.path.isfile(_TEMPLATE):
        raise RuntimeError('Thiếu template A3 (pha/assets/a3_template.pdf).')
    if not os.path.isfile(number_map_path):
        raise RuntimeError('Không tìm thấy ảnh bản đồ số.')

    doc = fitz.open(_TEMPLATE)
    page = doc[0]

    # Cỡ ảnh -> tỉ lệ, khớp VÀO ô (không méo), canh GIỮA ngang + sát TRÊN như mẫu.
    from PIL import Image
    with Image.open(number_map_path) as im:
        mw, mh = im.size
    bx0, by0, bx1, by1 = _BOX
    box_w, box_h = bx1 - bx0, by1 - by0
    r = mw / float(mh)
    if r >= box_w / box_h:                       # rộng hơn ô -> chặn theo BỀ RỘNG
        draw_w, draw_h = box_w, box_w / r
    else:                                        # cao hơn ô -> chặn theo CHIỀU CAO
        draw_h, draw_w = box_h, box_h * r
    x0 = bx0 + (box_w - draw_w) / 2.0            # giữa ngang
    rect = fitz.Rect(x0, by0, x0 + draw_w, by0 + draw_h)
    page.insert_image(rect, filename=number_map_path, keep_proportion=False)

    if code:
        tw = fitz.get_text_length(code, fontname='helv', fontsize=_CODE_SIZE)
        page.insert_text((_CODE_RIGHT - tw, _CODE_BASELINE), code,
                         fontname='helv', fontsize=_CODE_SIZE, color=(0, 0, 0))

    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    return out_path


def make_a3_from_result(result_url, code):
    """result_url = URL bản đồ số (tuyệt đối https://.../media/... HOẶC tương đối
    /media/...) -> PDF A3 trong MEDIA_ROOT. Trả URL /media/... của PDF."""
    import re
    from urllib.parse import unquote
    u = unquote(result_url or '').split('?', 1)[0].split('#', 1)[0]
    rel = (u.split('/media/', 1)[1] if '/media/' in u else u).lstrip('/')
    root = os.path.normpath(str(settings.MEDIA_ROOT))
    src = os.path.normpath(os.path.join(root, rel))
    if not src.startswith(root + os.sep):        # chặn ../ thoát MEDIA_ROOT
        raise RuntimeError('Đường dẫn ảnh bản đồ số không hợp lệ.')
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', (code or 'tranh').strip()) or 'tranh'
    out_rel = f'{os.path.splitext(rel)[0]}_{safe}_A3.pdf'
    out_abs = os.path.join(root, out_rel)
    make_a3_sheet(src, safe, out_abs)
    return '/media/' + out_rel.replace(os.sep, '/')


@staff_required
def anh_a3(request):
    """BƯỚC TẮT: bản đồ số -> PDF A3 in sẵn (template xưởng + mã). Trả {file_url}."""
    result_url = request.GET.get('result_url') or request.GET.get('img_output') or ''
    code = request.GET.get('code') or request.GET.get('image_name') or 'tranh'
    if not result_url:
        return JsonResponse({'ok': False, 'error': 'Thiếu bản đồ số.'}, status=400)
    try:
        return JsonResponse({'ok': True, 'file_url': make_a3_from_result(result_url, code)})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)
