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
