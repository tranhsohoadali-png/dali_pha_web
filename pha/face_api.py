"""Endpoint phụ trợ cho trang Xử lý ảnh: DÒ KHUÔN MẶT nhanh để auto-gợi-ý preset
chân dung khi người dùng vừa chọn ảnh (chưa bấm xử lý).

Đặt RIÊNG khỏi views.py để không vướng các thay đổi đang làm dở trong views.py.
"""
import os
import tempfile

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from pha.views import staff_required


@csrf_exempt
@staff_required
def anh_detect_face(request):
    """POST 1 ảnh (field 'image') -> {'ok':True,'face':bool,'n':int}.

    Dùng cho gợi-ý preset 'Chân dung'. Lỗi đọc/không có mặt -> face=False.
    """
    if request.method != 'POST' or not request.FILES.get('image'):
        return JsonResponse({'ok': False, 'face': False, 'n': 0})

    upload = request.FILES['image']
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.bmp'):
        ext = '.jpg'
    fd, tmp = tempfile.mkstemp(suffix=ext, prefix='facechk_')
    n = 0
    try:
        with os.fdopen(fd, 'wb') as f:
            for chunk in upload.chunks():
                f.write(chunk)
        from pha.color_index_lib import count_faces
        n = count_faces(tmp)
    except Exception:
        n = 0
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return JsonResponse({'ok': True, 'face': n > 0, 'n': int(n)})
