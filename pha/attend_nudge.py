# -*- coding: utf-8 -*-
"""NHẮC CHẤM CÔNG qua Web Push — gọi định kỳ bằng cron để nhắc nhân viên
bấm VÀO LÀM (đầu giờ) và TAN LÀM (cuối giờ), tránh quên chấm công.

Cron trên VPS (vd mỗi 10 phút trong giờ hành chính):
    */10 6-20 * * *  curl -s "https://mau.tranhdali.vn/cham-cong-nhac?key=<NUDGE_KEY>" >/dev/null

Endpoint tự bỏ qua ngày nghỉ cuối tuần + ngày lễ VN, và chỉ nhắc trong khung giờ
quanh giờ vào / giờ ra. Mỗi người chỉ nhắc 1 lần/ngày cho mỗi loại (in/out)."""
import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


def _nudge_key():
    from pha.models import AppSetting
    k = (AppSetting.get('NUDGE_KEY', '') or '').strip()
    if not k:
        import secrets
        k = 'nudge_' + secrets.token_urlsafe(16)
        AppSetting.set('NUDGE_KEY', k)
    return k


def _sent_today(day):
    from pha.models import AppSetting
    try:
        return json.loads(AppSetting.get('NUDGE_SENT_' + day, '') or '{}')
    except Exception:
        return {}


def _save_sent(day, data):
    from pha.models import AppSetting
    AppSetting.set('NUDGE_SENT_' + day, json.dumps(data))


@csrf_exempt
def nudge(request):
    """Gửi nhắc chấm công. Cron dùng ?key=; quản lý đăng nhập có thể bấm thử (?event=in|out&force=1)."""
    from pha import views, push
    from pha.models import Attendance, PushSubscription

    is_staff = bool(getattr(request.user, 'is_staff', False))
    if not is_staff and request.GET.get('key', '') != _nudge_key():
        return JsonResponse({'ok': False, 'error': 'Sai khoá'}, status=401)

    now = views._now()
    day = now.strftime('%Y-%m-%d')
    cfg = views._att_cfg()
    holiday = views._holiday_name(day)
    weekend = now.weekday() not in cfg['workdays']
    force = request.GET.get('force') == '1'
    event = request.GET.get('event')   # 'in' | 'out' | None (tự quyết theo giờ)

    if (weekend or holiday) and not force:
        return JsonResponse({'ok': True, 'skip': holiday or 'cuối tuần', 'sent_in': 0, 'sent_out': 0})

    start = views._hm_to_min(cfg['start'])
    end = views._hm_to_min(cfg['end'])
    nowm = now.hour * 60 + now.minute
    do_in = do_out = False
    if event == 'in':
        do_in = True
    elif event == 'out':
        do_out = True
    else:
        if start is not None and start - 10 <= nowm <= start + 45:
            do_in = True
        if end is not None and end - 5 <= nowm <= end + 120:
            do_out = True

    if not push.is_available():
        return JsonResponse({'ok': False, 'error': 'Máy chủ chưa cài pywebpush'})

    # Trạng thái chấm công hôm nay
    checked_in, checked_out = set(), set()
    for r in Attendance.objects.filter(day=day):
        if r.check_in:
            checked_in.add(r.user)
        if r.check_out:
            checked_out.add(r.user)

    sent = _sent_today(day)
    sent_in = set(sent.get('in', []))
    sent_out = set(sent.get('out', []))

    # Tất cả nhân viên có đăng ký nhận thông báo
    sub_users = {}
    for s in PushSubscription.objects.all():
        sub_users.setdefault(s.username, []).append(s)

    n_in = n_out = 0
    if do_in:
        payload = json.dumps({'title': '⏰ Nhắc chấm công VÀO',
                              'body': 'Đừng quên bấm VÀO LÀM nhé! Chúc một ngày làm việc vui vẻ 💪',
                              'url': '/cham-cong', 'tag': 'nudge-in-' + day, 'icon': '/media/icon-192.png'})
        for u, subs in sub_users.items():
            if u in checked_in:
                continue
            if not force and u in sent_in:
                continue
            for s in subs:
                push._send_one(s, payload)
            sent_in.add(u); n_in += 1
    if do_out:
        payload = json.dumps({'title': '🌆 Nhắc chấm công RA',
                              'body': 'Tan làm rồi — đừng quên bấm TAN LÀM để chấm công nhé! 👋',
                              'url': '/cham-cong', 'tag': 'nudge-out-' + day, 'icon': '/media/icon-192.png'})
        for u, subs in sub_users.items():
            if u not in checked_in or u in checked_out:
                continue   # chỉ nhắc người đã vào mà chưa ra
            if not force and u in sent_out:
                continue
            for s in subs:
                push._send_one(s, payload)
            sent_out.add(u); n_out += 1

    if not force:
        _save_sent(day, {'in': sorted(sent_in), 'out': sorted(sent_out)})
    return JsonResponse({'ok': True, 'day': day, 'do_in': do_in, 'do_out': do_out,
                         'sent_in': n_in, 'sent_out': n_out,
                         'subscribers': len(sub_users)})
