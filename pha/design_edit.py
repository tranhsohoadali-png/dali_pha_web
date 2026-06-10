"""Chỉnh ẢNH THIẾT KẾ (bản màu) trực tiếp: làm mượt đường nét theo 2 thanh
Paths (mượt nét) + Corners (bo góc) — giống Image Trace của Illustrator. Tách
RIÊNG khỏi views.py.

Luôn làm mượt từ ẢNH GỐC (không tích luỹ): mỗi lần kéo slider, client gửi đường
dẫn thiết kế GỐC + 2 giá trị -> server median trên nhãn màu -> trả ảnh mới.
"""
import os

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from pha.views import staff_required


def _majority_smooth(arr, ksize):
    """Làm mượt biên vùng màu bằng LỌC ĐA SỐ (majority): mỗi pixel lấy MÀU chiếm
    đa số trong cửa sổ ksize -> gộp/bo vùng nhỏ, KHÔNG tạo màu lạ, KHÔNG tăng vùng
    (khác median-trên-nhãn vốn chèn nhãn lạ ở biên)."""
    import numpy as np
    import cv2
    flat = arr.reshape(-1, 3)
    colors, inv = np.unique(flat, axis=0, return_inverse=True)
    if len(colors) < 2 or len(colors) > 300 or ksize < 3:
        return arr
    H, W = arr.shape[:2]
    lbl = inv.reshape(H, W)
    best = np.zeros((H, W), np.int32)
    bestc = np.full((H, W), -1.0, np.float32)
    for ci in range(len(colors)):
        cnt = cv2.boxFilter((lbl == ci).astype(np.float32), -1, (ksize, ksize),
                            normalize=False, borderType=cv2.BORDER_REPLICATE)
        upd = cnt > bestc
        best[upd] = ci
        bestc[upd] = cnt[upd]
    return colors[best.reshape(-1)].reshape(arr.shape)


@csrf_exempt
@staff_required
def design_smooth(request):
    """POST file_url (ảnh thiết kế GỐC) + paths(0-100) + corners(0-100)
    -> làm mượt -> {'ok':True,'url':...}. paths/corners = 0 -> giữ nguyên."""
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    import numpy as np
    from PIL import Image
    import pha.color_index_lib as cil

    fu = (request.POST.get('file_url') or '').replace('/media/', '').strip()
    if not fu:
        return JsonResponse({'ok': False, 'msg': 'thiếu ảnh thiết kế'})

    def _i(key):
        try:
            return max(0, min(100, int(float(request.POST.get(key) or 0))))
        except (TypeError, ValueError):
            return 0
    paths = _i('paths')      # 0 = giữ nét, 100 = mượt nhất
    corners = _i('corners')  # 0 = giữ nguyên, 100 = bo góc nhiều

    src = os.path.join(settings.MEDIA_ROOT, fu)
    if not os.path.exists(src):
        return JsonResponse({'ok': False, 'msg': 'không tìm thấy ảnh thiết kế'})
    try:
        arr = np.array(Image.open(src).convert('RGB'))
        # Paths -> lọc đa số kernel lớn dần (mượt/đơn giản đường biên, ÍT vùng hơn).
        # Corners -> 1 lượt nhỏ hơn (bo tròn góc nhọn). Đa số -> không tạo màu lạ.
        kp = int(round(paths / 100.0 * 18))
        kp = (kp | 1) if kp >= 3 else 0
        kc = int(round(corners / 100.0 * 11))
        kc = (kc | 1) if kc >= 3 else 0
        if kp:
            arr = _majority_smooth(arr, kp)
        if kc:
            arr = _majority_smooth(arr, kc)
        out_rel = os.path.splitext(fu)[0] + '_sm.png'
        Image.fromarray(arr).save(os.path.join(settings.MEDIA_ROOT, out_rel))
    except Exception as e:
        return JsonResponse({'ok': False, 'msg': 'lỗi: ' + str(e)[:120]})
    return JsonResponse({'ok': True, 'url': '/media/' + out_rel})


# ===================== IMAGE TRACE -> VECTOR SVG =====================
# Tái tạo "Image Trace" của Illustrator bằng mã nguồn mở (OpenCV): từ ảnh đã
# posterize (màu phẳng) -> dò biên TỪNG vùng màu -> đơn giản hoá đường -> path SVG.

def _fmt_pt(x, y):
    return '%.1f,%.1f' % (x, y)


def _poly_to_d(pts, rnd):
    """Đổi 1 đa giác [[x,y],...] thành chuỗi path SVG.
    rnd=0 -> giữ góc nhọn (L). rnd>0 -> bo tròn góc bằng đường cong bậc 2 (Q)."""
    n = len(pts)
    if n < 3:
        return ''
    if rnd <= 0:
        d = 'M' + _fmt_pt(pts[0][0], pts[0][1])
        for x, y in pts[1:]:
            d += ' L' + _fmt_pt(x, y)
        return d + ' Z'
    frac = 0.5 * (rnd / 100.0)   # lùi tối đa nửa cạnh -> bo tròn mạnh nhất

    def lerp(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    seg = []
    for i in range(n):
        prev, cur, nxt = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        seg.append((lerp(cur, prev, frac), cur, lerp(cur, nxt, frac)))
    d = 'M' + _fmt_pt(seg[0][0][0], seg[0][0][1])
    for i in range(n):
        pin, cur, pout = seg[i]
        d += ' Q%s %s' % (_fmt_pt(cur[0], cur[1]), _fmt_pt(pout[0], pout[1]))
        nxt_pin = seg[(i + 1) % n][0]
        d += ' L' + _fmt_pt(nxt_pin[0], nxt_pin[1])
    return d + ' Z'


@csrf_exempt
@staff_required
def design_trace(request):
    """POST ảnh thiết kế -> trace từng vùng màu thành VECTOR SVG (mỗi màu 1 <path>).
    Nhận image_data (dataURL PNG từ canvas) HOẶC file_url (/media/..). Tham số:
      simplify(0-100): đơn giản hoá đường (cao = ít điểm, gọn hơn).
      round(0-100): bo tròn góc (0 = giữ sắc cạnh).
      minarea: bỏ đốm nhỏ hơn (px², mặc định 6).
    -> {'ok':True,'url':'/media/.._trace.svg','colors':n,'paths':k,'w':W,'h':H}"""
    if request.method != 'POST':
        return JsonResponse({'ok': False})
    import io
    import base64
    import time
    import numpy as np
    import cv2
    from PIL import Image

    def _i(key, d=0, lo=0, hi=100):
        try:
            return max(lo, min(hi, int(float(request.POST.get(key) or d))))
        except (TypeError, ValueError):
            return d
    simplify = _i('simplify', 30)
    rnd = _i('round', 0)
    minarea = _i('minarea', 6, 0, 1000000)

    # ----- Đọc ảnh: ưu tiên canvas hiện tại (image_data), nếu không thì file_url -----
    data = request.POST.get('image_data') or ''
    try:
        if data.startswith('data:'):
            raw = base64.b64decode(data.split(',', 1)[1])
            arr = np.array(Image.open(io.BytesIO(raw)).convert('RGB'))
            out_rel = 'trace_%d.svg' % int(time.time())
        else:
            fu = (request.POST.get('file_url') or '').replace('/media/', '').strip()
            src = os.path.join(settings.MEDIA_ROOT, fu)
            if not fu or not os.path.exists(src):
                return JsonResponse({'ok': False, 'msg': 'không tìm thấy ảnh thiết kế'})
            arr = np.array(Image.open(src).convert('RGB'))
            out_rel = os.path.splitext(fu)[0] + '_trace.svg'
    except Exception as e:
        return JsonResponse({'ok': False, 'msg': 'ảnh không hợp lệ: ' + str(e)[:100]})

    H, W = arr.shape[:2]
    flat = arr.reshape(-1, 3)
    colors, inv = np.unique(flat, axis=0, return_inverse=True)
    if len(colors) > 400:
        return JsonResponse({'ok': False, 'msg': 'Ảnh có %d màu — quá nhiều để trace. '
                             'Hãy posterize/đánh số (giảm còn vài chục màu) rồi trace.' % len(colors)})
    counts = np.bincount(inv, minlength=len(colors))
    lbl = inv.reshape(H, W).astype(np.int32)
    eps_factor = (simplify / 100.0) * 0.02   # 0 = bám pixel, 100 = đơn giản mạnh

    paths_xml, npaths = [], 0
    for idx in np.argsort(-counts):          # vùng lớn (nền) vẽ trước
        mask = (lbl == idx).astype(np.uint8)
        cnts, _hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        subs = []
        for cnt in cnts:
            if cv2.contourArea(cnt) < minarea:
                continue
            eps = max(0.5, eps_factor * cv2.arcLength(cnt, True))
            ap = cv2.approxPolyDP(cnt, eps, True).reshape(-1, 2).tolist()
            d = _poly_to_d(ap, rnd)
            if d:
                subs.append(d)
                npaths += 1
        if not subs:
            continue
        c = colors[idx]
        hexc = '#%02X%02X%02X' % (int(c[0]), int(c[1]), int(c[2]))
        paths_xml.append('<path d="%s" fill="%s" fill-rule="evenodd"/>' % (' '.join(subs), hexc))

    svg = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
           'shape-rendering="geometricPrecision">\n%s\n</svg>\n'
           % (W, H, W, H, '\n'.join(paths_xml)))
    try:
        out_abs = os.path.join(settings.MEDIA_ROOT, out_rel)
        os.makedirs(os.path.dirname(out_abs), exist_ok=True) if os.path.dirname(out_rel) else None
        with open(out_abs, 'w', encoding='utf-8') as f:
            f.write(svg)
    except Exception as e:
        return JsonResponse({'ok': False, 'msg': 'lỗi ghi SVG: ' + str(e)[:100]})
    return JsonResponse({'ok': True, 'url': '/media/' + out_rel.replace('\\', '/'),
                         'colors': int(len(colors)), 'paths': npaths, 'w': W, 'h': H})
