# -*- coding: utf-8 -*-
"""NGHỈ PHÉP + TRANH HỎNG (QC) + THI ĐUA + CHUÔNG XƯỞNG — tách module riêng
(views.py đã lớn; các tính năng này độc lập, chỉ dùng helper chung từ views)."""
import json
from datetime import datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt


# ===================== NGHỈ PHÉP =====================
def _approved_leave_days(month):
    """{user: set(ngày 'YYYY-MM-DD')} các ngày NGHỈ PHÉP ĐÃ DUYỆT rơi vào tháng đó."""
    import calendar
    from pha.models import LeaveRequest
    out = {}
    try:
        y, m = (int(x) for x in month.split('-'))
        month_end = '%04d-%02d-%02d' % (y, m, calendar.monthrange(y, m)[1])
    except (ValueError, TypeError):
        return out
    qs = LeaveRequest.objects.filter(status=LeaveRequest.STATUS_APPROVED,
                                     from_day__lte=month_end, to_day__gte=month + '-01')
    for lr in qs:
        try:
            a = datetime.strptime(lr.from_day, '%Y-%m-%d').date()
            b = datetime.strptime(lr.to_day, '%Y-%m-%d').date()
        except ValueError:
            continue
        d = a
        while d <= b:
            ds = d.strftime('%Y-%m-%d')
            if ds.startswith(month):
                out.setdefault(lr.user, set()).add(ds)
            d += timedelta(days=1)
    return out


def _on_leave_today(day):
    """set(user) đang nghỉ phép ĐÃ DUYỆT trong ngày 'YYYY-MM-DD' (để khỏi nhắc chấm công)."""
    from pha.models import LeaveRequest
    return set(LeaveRequest.objects.filter(status=LeaveRequest.STATUS_APPROVED,
                                           from_day__lte=day, to_day__gte=day)
               .values_list('user', flat=True))


def _notify_staff_leave(user, f, t, reason):
    """Báo quản lý có đơn xin nghỉ mới (web push, best-effort)."""
    try:
        from pha import push
        from pha.views import _fmt_day
        from pha.models import PushSubscription
        if not push.is_available():
            return
        staff = set(User.objects.filter(is_staff=True).values_list('username', flat=True))
        rng = _fmt_day(f) + ('' if f == t else ' → ' + _fmt_day(t))
        payload = json.dumps({'title': '📝 Đơn xin nghỉ phép',
                              'body': f'{user} xin nghỉ {rng}' + (f' · {reason}' if reason else ''),
                              'url': '/cham-cong-quan-ly', 'tag': 'leave-new',
                              'icon': '/media/icon-192.png'})
        for s in PushSubscription.objects.filter(username__in=staff):
            push._send_one(s, payload)
    except Exception:
        pass


def _notify_leave_decision(lr):
    """Báo nhân viên kết quả duyệt đơn nghỉ (web push, best-effort)."""
    try:
        from pha import push
        from pha.views import _fmt_day
        from pha.models import PushSubscription
        if not push.is_available():
            return
        ok = lr.status == lr.STATUS_APPROVED
        rng = _fmt_day(lr.from_day) + ('' if lr.from_day == lr.to_day else ' → ' + _fmt_day(lr.to_day))
        payload = json.dumps({'title': ('✅ Được duyệt nghỉ ' if ok else '❌ Từ chối nghỉ ') + rng,
                              'body': 'Quản lý đã ' + ('DUYỆT' if ok else 'TỪ CHỐI') + ' đơn xin nghỉ của bạn.',
                              'url': '/cham-cong', 'tag': f'leave-{lr.id}',
                              'icon': '/media/icon-192.png'})
        for s in PushSubscription.objects.filter(username=lr.user):
            push._send_one(s, payload)
    except Exception:
        pass


@csrf_exempt
@login_required(login_url='/login')
def nghi_phep(request):
    """Nhân viên gửi ĐƠN XIN NGHỈ PHÉP (từ trang chấm công). Quản lý duyệt ở trang quản lý."""
    from pha.models import LeaveRequest
    if request.method != 'POST':
        return redirect('/cham-cong')
    u = request.user.username
    f = (request.POST.get('from_day') or '').strip()
    t = (request.POST.get('to_day') or '').strip() or f
    reason = (request.POST.get('reason') or '').strip()[:300]
    try:
        d1 = datetime.strptime(f, '%Y-%m-%d').date()
        d2 = datetime.strptime(t, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'ok': False, 'msg': 'Ngày không hợp lệ.'})
    if d2 < d1:
        d1, d2 = d2, d1
        f, t = t, f
    if (d2 - d1).days > 30:
        return JsonResponse({'ok': False, 'msg': 'Khoảng nghỉ tối đa 30 ngày.'})
    from pha.views import _now
    if d1 < (_now().date() - timedelta(days=7)):
        return JsonResponse({'ok': False, 'msg': 'Chỉ xin nghỉ từ hôm nay (hoặc bù tối đa 7 ngày trước).'})
    dup = LeaveRequest.objects.filter(user=u, status=LeaveRequest.STATUS_PENDING,
                                      from_day__lte=t, to_day__gte=f).exists()
    if dup:
        return JsonResponse({'ok': False, 'msg': 'Bạn đã có đơn đang chờ duyệt trùng khoảng ngày này.'})
    LeaveRequest.objects.create(user=u, from_day=f, to_day=t, reason=reason)
    _notify_staff_leave(u, f, t, reason)
    return JsonResponse({'ok': True, 'msg': 'Đã gửi đơn xin nghỉ — chờ quản lý duyệt.'})


# ===================== TRANH HỎNG (QC) =====================
_DEFECT_STAGES = {'pha': 'Pha màu', 'rot': 'Rót màu', 'sx': 'Sản xuất', 'khac': 'Khác'}


@csrf_exempt
def tranh_hong(request):
    """QC TRANH HỎNG: ghi nhận sản phẩm lỗi + thống kê tỷ lệ lỗi (chỉ quản lý)."""
    from pha.views import _now, _fmt_day, _fmt_month, _norm_size, _paint_sizes
    from pha.models import DefectLog, Painting, PaintingProduction
    if not getattr(request.user, 'is_staff', False):
        return redirect('/login')
    now = _now()
    if request.method == 'POST':
        act = request.POST.get('action')
        if act == 'add':
            try:
                qty = max(1, int(request.POST.get('qty') or 1))
            except ValueError:
                qty = 1
            day = (request.POST.get('day') or '').strip() or now.strftime('%Y-%m-%d')
            try:
                datetime.strptime(day, '%Y-%m-%d')
            except ValueError:
                day = now.strftime('%Y-%m-%d')
            stage = (request.POST.get('stage') or 'khac').strip()
            if stage not in _DEFECT_STAGES:
                stage = 'khac'
            DefectLog.objects.create(
                day=day, month=day[:7],
                painting=(request.POST.get('painting') or '').strip().upper(),
                size=_norm_size(request.POST.get('size')),
                qty=qty, stage=stage,
                reason=(request.POST.get('reason') or '').strip()[:300],
                by_user=(request.POST.get('by_user') or '').strip(),
                reporter=request.user.username,
                note=(request.POST.get('note') or '').strip()[:300])
            messages.info(request, f'Đã ghi nhận {qty} tranh hỏng.')
            return redirect('/tranh-hong')
        if act == 'delete':
            DefectLog.objects.filter(id=request.POST.get('id')).delete()
            messages.info(request, 'Đã xoá bản ghi.')
            return redirect('/tranh-hong')

    month = request.GET.get('month') or now.strftime('%Y-%m')
    logs = list(DefectLog.objects.filter(month=month))
    total = sum(max(1, l.qty) for l in logs)
    by_stage, by_user, by_reason = {}, {}, {}
    for l in logs:
        q = max(1, l.qty)
        sname = _DEFECT_STAGES.get(l.stage, 'Khác')
        by_stage[sname] = by_stage.get(sname, 0) + q
        if l.by_user:
            by_user[l.by_user] = by_user.get(l.by_user, 0) + q
        if l.reason:
            by_reason[l.reason] = by_reason.get(l.reason, 0) + q
    sx_total = sum(max(1, int(p.qty or 1)) for p in PaintingProduction.objects.filter(month=month))
    rate = round(total / (sx_total + total) * 100, 1) if (sx_total + total) else 0.0
    rows = [{'id': l.id, 'day': _fmt_day(l.day), 'painting': l.painting, 'size': l.size,
             'qty': max(1, l.qty), 'stage': _DEFECT_STAGES.get(l.stage, 'Khác'),
             'reason': l.reason, 'by_user': l.by_user, 'reporter': l.reporter, 'note': l.note}
            for l in logs]
    months = sorted(set(DefectLog.objects.values_list('month', flat=True))
                    | {now.strftime('%Y-%m')}, reverse=True)
    emp_users = sorted(User.objects.values_list('username', flat=True))
    return render(request, 'tranh_hong.html', {
        'month': month, 'month_label': _fmt_month(month),
        'months': [{'value': m, 'label': _fmt_month(m)} for m in months],
        'total': total, 'rate': rate, 'sx_total': sx_total,
        'by_stage': sorted(by_stage.items(), key=lambda x: -x[1]),
        'by_user': sorted(by_user.items(), key=lambda x: -x[1]),
        'by_reason': sorted(by_reason.items(), key=lambda x: -x[1])[:10],
        'rows': rows[:300],
        'paint_codes': list(Painting.objects.values_list('code', flat=True)),
        'emp_users': emp_users, 'today_iso': now.strftime('%Y-%m-%d'),
        'sizes': _paint_sizes(),
    })


# ===================== THI ĐUA =====================
@csrf_exempt
@login_required(login_url='/login')
def thi_dua(request):
    """BẢNG THI ĐUA tuần này cho app nhân viên: xếp hạng theo tổng đầu việc (pha+rót+SX)."""
    from pha.views import _productivity
    label, rows, _totals = _productivity('week', None)
    me = request.user.username
    out = []
    for i, r in enumerate(rows[:10]):
        out.append({'rank': i + 1, 'user': r['user'], 'pha': r['pha'], 'rot_p': r['rot_p'],
                    'sx': r['sx'], 'out': r['out'], 'me': r['user'] == me})
    mine = next((r for r in out if r['me']), None)
    if mine is None:
        for i, r in enumerate(rows):
            if r['user'] == me:
                mine = {'rank': i + 1, 'user': me, 'pha': r['pha'], 'rot_p': r['rot_p'],
                        'sx': r['sx'], 'out': r['out'], 'me': True}
                break
    return JsonResponse({'label': label, 'rows': out, 'mine': mine})


# ===================== CHUÔNG XƯỞNG =====================
@login_required(login_url='/login')
def chuong(request):
    """Trang CHUÔNG XƯỞNG: đặt 1 điện thoại/máy tính bảng cắm loa ở xưởng, mở trang này
    và bấm 'Bật chuông' — chuông tự KÊU (âm thanh thật, không phải thông báo) trước giờ
    hết nghỉ trưa N phút để mọi người dậy."""
    return render(request, 'chuong.html', {})


@login_required(login_url='/login')
def chuong_config(request):
    """Cấu hình chuông cho client (giờ nghỉ trưa + số phút kêu trước + giờ máy chủ VN)."""
    from pha.views import _att_cfg, _now
    cfg = _att_cfg()
    now = _now()
    return JsonResponse({'lunch_start': cfg['lunch_start'], 'lunch_end': cfg['lunch_end'],
                         'bell_before': cfg['bell_before'], 'work_end': cfg['end'],
                         'server_hm': now.strftime('%H:%M'),
                         'server_day': now.strftime('%Y-%m-%d')})
