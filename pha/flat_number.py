"""ĐÁNH SỐ ẢNH PHẲNG (ảnh đã thiết kế sẵn) — tách RIÊNG khỏi views.py.

Quy trình 1-CHẠM cho ảnh đã làm phẳng (cel / vector / ảnh đã thiết kế xong):
  - KHÔNG dùng AI, KHÔNG mean-shift (smooth=0) -> GIỮ NGUYÊN thiết kế (không bệt).
  - Chỉ gộp đốm li ti (min_area) + đánh số + khớp mã DALI.

Endpoint JSON `/xu-ly-anh-phang`: nút "Đánh số ảnh phẳng" trên trang Xử lý ảnh
gọi bằng fetch, nhận {ok, file_url} rồi để trang tự poll /anh-result như luồng
thường (tái dùng getResult). Vì là API thuần nên KHÔNG phụ thuộc build_ctx của
views.py (tách hẳn). Quy ước: endpoint mới đặt module riêng vì views.py hay bị
sửa song song.
"""
from datetime import datetime

from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from pha.models import ImageResult
from pha.views import staff_required, _img_executor, _prune_image_results

# Mặc định cho ảnh ĐÃ THIẾT KẾ PHẲNG khi người dùng không tự điền 2 núm:
FLAT_DEFAULT_COLORS = 48    # giữ sát số màu thiết kế gốc (thường 40-48), không bung vô hạn
FLAT_DEFAULT_MIN_AREA = 20  # dọn đốm răng cưa li ti, không phá mảng thiết kế


def _clamp_int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


@csrf_exempt
@staff_required
def xu_ly_anh_phang(request):
    """1 chạm: số hoá ảnh ĐÃ thiết kế phẳng (ÉP không AI + không làm phẳng lại).

    Chỉ nhận 2 núm có ý nghĩa cho ảnh phẳng: color_limit (số màu tối đa) và
    min_area (bỏ mảng nhỏ hơn px). Mọi thứ khác bị ép cố định. Trả JSON
    {ok, file_url} để trang tự poll /anh-result như luồng thường.
    """
    from pha.imageproc import process_image
    if request.method != 'POST' or not request.FILES.get('image'):
        return JsonResponse({'ok': False, 'msg': 'Thiếu ảnh.'})

    upload = request.FILES['image']
    fss = FileSystemStorage()
    name = f'{datetime.now():%Y-%m-%d_%H-%M-%S}_{upload.name}'
    fss.save(name, upload)

    color_limit = _clamp_int(request.POST.get('color_limit'), 0, 60, FLAT_DEFAULT_COLORS)
    if color_limit <= 0:                       # 0/không hợp lệ -> giữ sát thiết kế, không bung vô hạn
        color_limit = FLAT_DEFAULT_COLORS
    min_area = _clamp_int(request.POST.get('min_area'), 0, 100000, FLAT_DEFAULT_MIN_AREA)
    size_str = (request.POST.get('print_size') or '40x50').strip()
    try:
        dims = [int(x) for x in size_str.lower().replace(' ', '').split('x') if x]
        print_long_cm = max(dims) if dims else 0
    except ValueError:
        print_long_cm = 0

    rec = ImageResult.objects.create(
        name=name, status=ImageResult.STATUS_PROCESSING,
        user=getattr(request.user, 'username', ''),
        params={'enhance': False, 'color_limit': color_limit, 'min_area': min_area,
                'smooth': 0, 'style_category': '', 'preset': 'phang',
                'print_size': size_str, 'flat': True})
    # ÉP: enhance=False, style=None, smooth=0, ai_prompt=None, use_refs=False.
    _img_executor.submit(process_image, rec.id, name, False, None,
                         color_limit, min_area, 0, None, False, print_long_cm)
    _prune_image_results()                     # giữ 10 kết quả gần nhất (bộ nhớ tạm)
    return JsonResponse({'ok': True, 'file_url': name})
