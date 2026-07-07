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


def _staff(request):
    return getattr(request.user, 'is_staff', False)


# Các trạng thái tính là "đã gửi đi in" (để cộng mét vải đã dùng)
_PRINTED_STATES = ('sent', 'ripping', 'done', 'printed')


def rip_list(request):
    """UI web poll: danh sách job + trạng thái (chỉ nhân viên).
    show=active (mặc định): ẩn job đã đánh dấu in xong & job lỗi cũ. show=all: hiện hết."""
    if not _staff(request):
        return HttpResponseForbidden('staff only')
    from pha.models import PrintJob
    from django.utils import timezone
    show = request.GET.get('show', 'active')
    qs = PrintJob.objects.all()
    if show != 'all':
        qs = qs.exclude(status=PrintJob.PRINTED)
    jobs = []
    for j in qs[:50]:
        # created lưu UTC (USE_TZ=True) -> phải đổi sang giờ VN trước khi format, nếu không lệch -7h
        _ct = timezone.localtime(j.created) if j.created else None
        jobs.append({'id': j.id, 'name': j.name, 'meters': round(j.meters, 2),
                     'util': j.util, 'count': j.count, 'status': j.status,
                     'message': j.message, 'prt_mb': round(j.prt_mb, 1),
                     'created': _ct.strftime('%H:%M %d/%m') if _ct else ''})
    return JsonResponse({'ok': True, 'jobs': jobs, 'show': show, 'key': _rip_key()})


@csrf_exempt
def rip_action(request):
    """Nhân viên thao tác 1 job: act = delete | printed | repend | unprint.
    - delete: xoá hẳn (job lỗi/nhầm).
    - printed: đánh dấu ĐÃ IN xong (đưa vào lịch sử, tính mét vải).
    - resend: tạo lại job (pending) để gửi sang Flexi lần nữa.
    """
    if request.method != 'POST' or not _staff(request):
        return HttpResponseForbidden('staff only')
    from pha.models import PrintJob
    j = PrintJob.objects.filter(id=request.POST.get('id')).first()
    if not j:
        return JsonResponse({'ok': False, 'msg': 'Không thấy job.'})
    act = (request.POST.get('act') or '').strip()
    if act == 'delete':
        j.delete()
        return JsonResponse({'ok': True})
    if act == 'printed':
        j.status = PrintJob.PRINTED
        j.save()
        return JsonResponse({'ok': True})
    if act == 'unprint':
        j.status = PrintJob.DONE
        j.save()
        return JsonResponse({'ok': True})
    if act == 'resend':
        clone = PrintJob.objects.create(name=j.name, pdf=j.pdf, meters=j.meters,
                                        util=j.util, count=j.count, status=PrintJob.PENDING)
        return JsonResponse({'ok': True, 'id': clone.id})
    return JsonResponse({'ok': False, 'msg': 'Lệnh không hợp lệ.'})


def rip_stats(request):
    """Thống kê mét vải đã gửi in + ước tính chi phí (chỉ nhân viên)."""
    if not _staff(request):
        return HttpResponseForbidden('staff only')
    from datetime import timedelta
    from pha.models import PrintJob, AppSetting
    try:
        from pha.views import _now
        now = _now()
    except Exception:
        from django.utils import timezone
        now = timezone.localtime()
    qs = PrintJob.objects.filter(status__in=_PRINTED_STATES)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def msum(since):
        return round(sum(j.meters for j in qs.filter(created__gte=since)), 2)

    cost = 0.0
    cobj = AppSetting.objects.filter(key='PRINT_COST_PER_M').first()
    if cobj:
        try:
            cost = float(str(cobj.value).replace(',', '').strip() or 0)
        except ValueError:
            cost = 0.0
    m_today, m_week, m_month = msum(day0), msum(now - timedelta(days=7)), msum(now - timedelta(days=30))
    m_total = round(sum(j.meters for j in qs), 2)
    return JsonResponse({
        'ok': True, 'cost_per_m': cost,
        'today': m_today, 'week': m_week, 'month': m_month, 'total': m_total,
        'cost_today': round(m_today * cost), 'cost_month': round(m_month * cost),
        'pending': PrintJob.objects.filter(status=PrintJob.PENDING).count(),
        'inprogress': PrintJob.objects.filter(status__in=('sent', 'ripping', 'done')).count(),
        'printed': PrintJob.objects.filter(status=PrintJob.PRINTED).count(),
    })


@csrf_exempt
def rip_cost(request):
    """Nhân viên đặt đơn giá vải (VNĐ/mét) để ước tính chi phí."""
    if request.method != 'POST' or not _staff(request):
        return HttpResponseForbidden('staff only')
    from pha.models import AppSetting
    val = str(request.POST.get('cost_per_m') or '0').replace(',', '').strip()
    try:
        float(val or 0)
    except ValueError:
        return JsonResponse({'ok': False, 'msg': 'Đơn giá không hợp lệ.'})
    AppSetting.objects.update_or_create(key='PRINT_COST_PER_M', defaults={'value': val})
    return JsonResponse({'ok': True, 'cost_per_m': float(val or 0)})
