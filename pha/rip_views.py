# -*- coding: utf-8 -*-
"""HÀNG ĐỢI RIP — cầu nối Web (Ghép in) <-> DALI Print Agent <-> Flexi.

Web KHÔNG tự RIP (.prt là dữ liệu riêng của máy in EPS3200, chỉ Flexi tạo được).
Web chỉ ĐIỀU KHIỂN: tạo job (pending) -> Agent trên máy in kéo PDF về thả vào hot
folder Flexi -> Flexi RIP -> Agent báo trạng thái ngược lại.

Đặt riêng module (không nhét vào views.py) vì views.py hay bị sửa song song.
"""
import hmac
import os

from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt


def _rip_key():
    """Khoá API cho Agent (tự sinh & lưu trong AppSetting nếu chưa có)."""
    from pha.models import AppSetting
    obj, _ = AppSetting.objects.get_or_create(
        key='RIP_API_KEY',
        defaults={'value': 'rip-' + os.urandom(8).hex()})
    if not obj.value:
        obj.value = 'rip-' + os.urandom(8).hex()
        obj.save()
    return obj.value


def _check_key(request):
    given = request.headers.get('X-API-Key') or request.GET.get('key') or request.POST.get('key') or ''
    return hmac.compare_digest(str(given), str(_rip_key()))


def _abs_pdf_url(request, rel):
    """URL tuyệt đối để Agent tải PDF (kể cả khi ở sau proxy https)."""
    rel = (rel or '').lstrip('/')
    if not rel.startswith('media/'):
        rel = 'media/' + rel
    return request.build_absolute_uri('/' + rel)


@csrf_exempt
def rip_queue(request):
    """Agent gọi GET (kèm key) -> danh sách job ĐANG CHỜ gửi (pending). Đánh dấu đã lấy
    (-> sent) để không lấy lại; Agent báo lại trạng thái qua rip_status."""
    if not _check_key(request):
        return HttpResponseForbidden('bad key')
    from pha.models import PrintJob
    jobs = list(PrintJob.objects.filter(status=PrintJob.PENDING).order_by('created')[:20])
    out = []
    for j in jobs:
        out.append({'id': j.id, 'name': j.name, 'pdf_url': _abs_pdf_url(request, j.pdf),
                    'meters': j.meters, 'count': j.count})
    return JsonResponse({'ok': True, 'jobs': out})


@csrf_exempt
def rip_status(request):
    """Agent POST cập nhật trạng thái: id, status (sent|ripping|done|error), message, prt_mb."""
    if request.method != 'POST' or not _check_key(request):
        return HttpResponseForbidden('bad key')
    from pha.models import PrintJob
    j = PrintJob.objects.filter(id=request.POST.get('id')).first()
    if not j:
        return JsonResponse({'ok': False, 'msg': 'job not found'})
    st = (request.POST.get('status') or '').strip()
    if st in (PrintJob.PENDING, PrintJob.SENT, PrintJob.RIPPING, PrintJob.DONE, PrintJob.ERROR):
        j.status = st
    j.message = (request.POST.get('message') or j.message)[:300]
    try:
        j.prt_mb = float(request.POST.get('prt_mb') or j.prt_mb)
    except (TypeError, ValueError):
        pass
    j.save()
    return JsonResponse({'ok': True, 'id': j.id, 'status': j.status})


def rip_list(request):
    """UI web poll: danh sách job gần đây + trạng thái (chỉ nhân viên)."""
    if not getattr(request.user, 'is_staff', False):
        return HttpResponseForbidden('staff only')
    from pha.models import PrintJob
    jobs = []
    for j in PrintJob.objects.all()[:30]:
        jobs.append({'id': j.id, 'name': j.name, 'meters': round(j.meters, 2),
                     'util': j.util, 'count': j.count, 'status': j.status,
                     'message': j.message, 'prt_mb': round(j.prt_mb, 1),
                     'created': j.created.strftime('%H:%M %d/%m')})
    return JsonResponse({'ok': True, 'jobs': jobs, 'key': _rip_key()})
