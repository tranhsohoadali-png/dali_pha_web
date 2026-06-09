"""KHO HỌC (Giai đoạn A của hệ tự-học): lưu lại các ca xử lý ảnh ĐÃ DUYỆT để
hệ thống học dần. Tách RIÊNG khỏi views.py.

Luồng:
- Người dùng xử lý 1 ảnh -> ImageResult (tạm, bị prune còn 10).
- Bấm "Lưu vào kho học" -> COPY file (ảnh gốc/AI/thiết kế/bản số) sang
  MEDIA_ROOT/training_data/ (vĩnh viễn) + tính chữ ký ảnh gốc -> TrainingSample.
- TrainingSample KHÔNG bị prune -> tích luỹ dữ liệu cho Giai đoạn B (gợi ý theo
  độ giống) và xa hơn (fine-tune).
"""
import os
import shutil

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from pha.models import ImageResult, TrainingSample
from pha.views import staff_required

TRAIN_SUBDIR = 'training_data'


def _train_dir():
    d = os.path.join(settings.MEDIA_ROOT, TRAIN_SUBDIR)
    os.makedirs(d, exist_ok=True)
    return d


def _copy_into_train(fn):
    """Copy 1 file (đường dẫn tương đối trong MEDIA_ROOT) sang training_data/.
    Trả đường dẫn tương đối mới, hoặc '' nếu không có/không copy được."""
    if not fn:
        return ''
    src = os.path.join(settings.MEDIA_ROOT, fn)
    if not os.path.exists(src):
        return ''
    base = os.path.basename(fn)
    dst_rel = f'{TRAIN_SUBDIR}/{base}'
    try:
        _train_dir()
        shutil.copyfile(src, os.path.join(settings.MEDIA_ROOT, dst_rel))
    except OSError:
        return ''
    return dst_rel


def save_training_sample(res, user='', note=''):
    """Lưu 1 ImageResult ĐÃ HOÀN TẤT vào KHO HỌC. Idempotent theo ảnh gốc: nếu đã
    lưu rồi thì trả lại bản cũ (created=False). Trả (TrainingSample, created)."""
    from PIL import Image
    from pha.style_library import signature

    source_rel = _copy_into_train(res.name)
    if not source_rel:
        raise ValueError('Không tìm thấy ảnh gốc để lưu.')

    existing = TrainingSample.objects.filter(source_name=source_rel).first()
    if existing:
        return existing, False

    enhanced_rel = _copy_into_train(res.enhanced_name)
    design_rel = _copy_into_train(res.design_name)
    result_rel = _copy_into_train(res.name_output)
    try:
        sig = signature(Image.open(os.path.join(settings.MEDIA_ROOT, source_rel)))
    except Exception:
        sig = []
    ts = TrainingSample.objects.create(
        source_name=source_rel, enhanced_name=enhanced_rel, design_name=design_rel,
        result_name=result_rel, params=(res.params or {}), colors=(res.colors or []),
        sig=sig, note=(note or '')[:200], user=user or '')
    return ts, True


@csrf_exempt
@staff_required
def save_sample(request):
    """POST file_url (+ note) -> lưu kết quả vào kho học. Trả JSON."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'msg': 'POST only'})
    file_url = (request.POST.get('file_url') or '').replace('/media/', '').strip()
    if not file_url:
        return JsonResponse({'ok': False, 'msg': 'Thiếu ảnh.'})
    res = (ImageResult.objects.filter(name=file_url)
           .order_by('-created_time').first())
    if not res or res.status != ImageResult.STATUS_DONE:
        return JsonResponse({'ok': False, 'msg': 'Chưa có kết quả hoàn tất để lưu.'})
    note = (request.POST.get('note') or '').strip()
    try:
        ts, created = save_training_sample(res, user=getattr(request.user, 'username', ''), note=note)
    except Exception as e:
        return JsonResponse({'ok': False, 'msg': 'Lỗi lưu: ' + str(e)[:160]})
    total = TrainingSample.objects.count()
    if not created:
        return JsonResponse({'ok': True, 'id': ts.id, 'count': total, 'dup': True,
                             'msg': f'Ảnh này đã có trong kho học (#{ts.id}). Kho: {total} mẫu.'})
    return JsonResponse({'ok': True, 'id': ts.id, 'count': total,
                         'msg': f'Đã lưu vào kho học (#{ts.id}). Kho: {total} mẫu.'})


@staff_required
def kho_hoc(request):
    """Trang xem KHO HỌC: danh sách mẫu đã lưu (ảnh gốc · thiết kế · thông số)."""
    rows = list(TrainingSample.objects.all()[:300])
    for r in rows:
        r.n_colors = len(r.colors or [])
        try:
            r.dt = r.created_time.strftime('%d/%m %H:%M')
        except Exception:
            r.dt = ''
    return render(request, 'kho_hoc.html', {'rows': rows, 'total': TrainingSample.objects.count()})


@csrf_exempt
@staff_required
def delete_sample(request):
    """POST id -> xoá 1 mẫu khỏi kho học + file kèm theo."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'msg': 'POST only'})
    ts = TrainingSample.objects.filter(id=request.POST.get('id')).first()
    if not ts:
        return JsonResponse({'ok': False, 'msg': 'Không tìm thấy mẫu.'})
    for fn in (ts.source_name, ts.enhanced_name, ts.design_name, ts.result_name):
        if fn:
            try:
                os.remove(os.path.join(settings.MEDIA_ROOT, fn))
            except OSError:
                pass
    ts.delete()
    return JsonResponse({'ok': True, 'msg': 'Đã xoá 1 mẫu.', 'count': TrainingSample.objects.count()})
