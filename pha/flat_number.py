"""ĐÁNH SỐ ẢNH PHẲNG (ảnh đã thiết kế sẵn) — tách RIÊNG khỏi views.py.

Gộp ĐÚNG 2 phần mềm gốc của DALI cho ảnh ĐÃ thiết kế phẳng:
  - `index_color` (đánh số): GIỮ NGUYÊN bảng màu thiết kế — KHÔNG quantize / KHÔNG
    giảm màu. Lấy màu gốc -> contour từng màu -> polylabel -> vẽ số.
  - `django_dali` (khớp mã): mỗi màu khớp mã DALI gần nhất (nearest_dali).
KHÔNG dùng AI, KHÔNG mean-shift, KHÔNG núm nào cả — GIỮ NGUYÊN 100% ảnh đưa vào
(đúng index_color gốc: chỉ tự lọc màu chiếm < 0.03% diện tích).

Endpoint JSON `/xu-ly-anh-phang`: card "Đánh số ảnh phẳng" trên trang Xử lý ảnh gọi
bằng fetch, nhận {ok, file_url} rồi để trang tự poll /anh-result (tái dùng getResult).
Là API thuần nên KHÔNG phụ thuộc build_ctx của views.py (tách hẳn). Quy ước: endpoint
mới đặt module riêng vì views.py hay bị sửa song song.
"""
import os
from datetime import datetime

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from pha.models import ImageResult
from pha.views import staff_required, _img_executor, _prune_image_results

def process_flat_image(rec_id, name):
    """Chạy nền: số hoá ảnh ĐÃ thiết kế phẳng (giữ nguyên 100% màu) + khớp mã DALI.
    KHÔNG AI, KHÔNG quantize, KHÔNG gộp mảng. Cập nhật ImageResult để trang poll."""
    from pha.color_index_lib import index_color_flat
    from pha.imageproc import save_img, convert_to_hex, create_image_color
    from PIL import Image
    obj = ImageResult.objects.get(id=rec_id)
    try:
        path = os.path.join(settings.MEDIA_ROOT, name)
        design_name = f'{os.path.splitext(name)[0]}_design.png'
        design_path = os.path.join(settings.MEDIA_ROOT, design_name)
        edge_img, color_mapping, percentages = index_color_flat(
            path, min_area=0, design_out=design_path)
        dpi = Image.open(path).info.get('dpi', (72, 72))
        name_output = save_img(edge_img, dpi)
        colors = create_image_color(color_mapping, convert_to_hex(color_mapping), percentages)
        obj.name_output = name_output
        obj.design_name = design_name if os.path.exists(design_path) else ''
        obj.colors = colors
        obj.status = ImageResult.STATUS_DONE
        obj.error_message = ''
        obj.save()
    except Exception as e:
        obj.status = ImageResult.STATUS_ERROR
        obj.error_message = str(e)
        obj.save()


@staff_required
def anh_phang(request):
    """Trang TAB RIÊNG "Đánh số ảnh phẳng" (menu Xử lý ảnh). Chỉ hiển thị: upload
    + nút bấm + kết quả (KHÔNG núm nào); xử lý qua /xu-ly-anh-phang. Lịch sử chỉ
    liệt kê các ca PHẲNG (params.flat=True) cho đỡ lẫn với ca AI."""
    from pha.views import _fmt_name
    last = [r for r in ImageResult.objects.all().order_by('-created_time')[:30]
            if (r.params or {}).get('flat')][:10]
    return render(request, 'anh_phang.html',
                  {'last_query': [{'name': _fmt_name(q.name), 'url': q.name} for q in last]})


@csrf_exempt
@staff_required
def xu_ly_anh_phang(request):
    """1 chạm, KHÔNG núm nào: số hoá ảnh ĐÃ thiết kế phẳng — GIỮ NGUYÊN 100% ảnh
    đưa vào (không AI, không giảm màu, không gộp mảng). Trả JSON {ok, file_url}
    để trang tự poll /anh-result như luồng thường."""
    if request.method != 'POST' or not request.FILES.get('image'):
        return JsonResponse({'ok': False, 'msg': 'Thiếu ảnh.'})

    upload = request.FILES['image']
    fss = FileSystemStorage()
    name = f'{datetime.now():%Y-%m-%d_%H-%M-%S}_{upload.name}'
    fss.save(name, upload)

    rec = ImageResult.objects.create(
        name=name, status=ImageResult.STATUS_PROCESSING,
        user=getattr(request.user, 'username', ''),
        params={'enhance': False, 'min_area': 0, 'smooth': 0,
                'preset': 'phang', 'flat': True})
    _img_executor.submit(process_flat_image, rec.id, name)
    _prune_image_results()                     # giữ 10 kết quả gần nhất (bộ nhớ tạm)
    return JsonResponse({'ok': True, 'file_url': name})
