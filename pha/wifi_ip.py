# -*- coding: utf-8 -*-
"""TỰ CẬP NHẬT IP WIFI XƯỞNG cho chấm công (khỏi sửa tay mỗi lần ISP đổi IP).

Ý tưởng: **DALI Print Agent chạy TẠI XƯỞNG** (trên máy in) và poll `/api/rip-queue`
mỗi 3 giây kèm khoá. Vậy IP công cộng mà server thấy ở request đó **chính là IP WiFi
xưởng**. Chỉ cần ghi lại khi nó ĐỔI -> danh sách IP chấm công luôn đúng.

An toàn (đã qua rà soát đối kháng — 3 lỗi tìm thấy & đã vá):
- Chỉ ghi từ endpoint ĐÃ KIỂM KHOÁ (rip_queue). Không tin `X-Forwarded-For` do client
  gửi: dùng `views._client_ip()` lấy entry CUỐI (do nginx tự thêm).
- CHỈ nhận IPv4 công cộng (`is_public_ip`). IPv6 bị chặn: không có NAT nên mỗi máy một
  địa chỉ riêng -> ghi IPv6 của agent = khoá toàn bộ chấm công.
- LUÔN ghi nhận IP agent báo (SEEN) để đối chiếu, nhưng chỉ ÁP DỤNG khi bật AUTO_ON
  (mặc định TẮT — phải bật có chủ đích, tránh bỗng dưng siết IP làm nhân viên bị chặn).
- BẬT chỉ khi agent ĐANG online (nhịp tim K_SEEN_LAST, throttle 60s). Không dùng
  K_SEEN_AT để đo "sống" vì nó chỉ ghi khi IP ĐỔI.
- ÂN HẠN: IP cũ vẫn cho phép thêm GRACE_HOURS giờ sau khi đổi -> không chặn giữa ca.
- Nút "Dùng IP hiện tại" (chỉ quản lý) làm dự phòng khi agent tắt.
- `wifi_ip_clear()` PHẢI tắt AUTO_ON, không thì agent ghi lại IP sai sau ~3 giây.

Đặt ở module riêng để không đụng `views.py` (user hay sửa song song).
"""
import ipaddress
from datetime import timedelta

from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone

GRACE_HOURS = 12          # IP cũ còn dùng được ngần này giờ sau khi đổi


AGENT_ONLINE_S = 300      # agent coi như còn sống nếu báo trong 5 phút gần đây
_HEARTBEAT_S = 60         # ghi "nhịp tim" tối đa 1 lần/phút (agent poll 3s -> khỏi ghi DB liên tục)


def is_public_ip(ip):
    """CHỈ chấp nhận IPv4 công cộng. Chặn: nội bộ (10/192.168/172.16-31), loopback,
    link-local, multicast, reserved, 0.0.0.0, broadcast, chuỗi rác — VÀ CẢ IPv6.

    Vì sao chặn IPv6: cơ chế này dựa trên giả định "cả xưởng ra Internet bằng MỘT IP
    công cộng" (NAT của IPv4). IPv6 KHÔNG có NAT — máy in và mỗi điện thoại nhân viên
    có địa chỉ toàn cầu RIÊNG. Nếu ghi IPv6 của agent làm IP xưởng thì không khớp máy
    nhân viên nào -> KHOÁ TOÀN BỘ chấm công.
    Chặt hơn `views._ip_private()` (chỉ so tiền tố) — vì IP này trở thành allow-list chấm công."""
    try:
        a = ipaddress.ip_address((ip or '').strip())
    except ValueError:
        return False
    if a.version != 4:
        return False
    return bool(a.is_global) and not a.is_multicast

K_SEEN = 'ATTENDANCE_IP_SEEN'            # IP agent báo gần nhất (chỉ để hiển thị)
K_SEEN_AT = 'ATTENDANCE_IP_SEEN_AT'
K_SEEN_LAST = 'ATTENDANCE_IP_SEEN_LAST'  # nhịp tim: lần cuối agent báo (biết agent còn sống)
K_AUTO = 'ATTENDANCE_IP_AUTO'            # IP xưởng ĐANG áp dụng cho chấm công
K_AUTO_AT = 'ATTENDANCE_IP_AUTO_AT'
K_PREV = 'ATTENDANCE_IP_AUTO_PREV'       # IP trước đó (còn hiệu lực trong ân hạn)
K_PREV_AT = 'ATTENDANCE_IP_AUTO_PREV_AT'
K_ON = 'ATTENDANCE_IP_AUTO_ON'           # '1' = cho agent tự ghi


def _S():
    from pha.models import AppSetting
    return AppSetting


def _get(key, default=''):
    return (_S().get(key, default) or '').strip()


def _parse(iso):
    """Đọc mốc thời gian đã lưu (ISO, có timezone). Lỗi -> None."""
    if not iso:
        return None
    try:
        from datetime import datetime
        d = datetime.fromisoformat(iso)
        return d if d.tzinfo else timezone.make_aware(d)
    except (ValueError, TypeError):
        return None


def is_on():
    return _get(K_ON, '0') == '1'


def auto_ips():
    """IP xưởng tự động ĐANG có hiệu lực: IP hiện tại + IP trước (nếu còn ân hạn).
    `views._ip_allowed()` gọi hàm này. An toàn tuyệt đối: lỗi -> trả [] (không nới lỏng)."""
    try:
        out = []
        cur = _get(K_AUTO)
        if cur:
            out.append(cur)
        prev, at = _get(K_PREV), _parse(_get(K_PREV_AT))
        if prev and at and (timezone.now() - at) <= timedelta(hours=GRACE_HOURS):
            out.append(prev)
        return out
    except Exception:
        return []


def set_workshop_ip(ip):
    """Đặt IP xưởng = ip, đẩy IP cũ sang ân hạn. Trả True nếu THỰC SỰ đổi."""
    cur = _get(K_AUTO)
    if ip == cur:
        return False
    if cur:
        _S().set(K_PREV, cur)
        _S().set(K_PREV_AT, timezone.now().isoformat())
    _S().set(K_AUTO, ip)
    _S().set(K_AUTO_AT, timezone.now().isoformat())
    return True


def remember(request):
    """Gọi từ endpoint ĐÃ KIỂM KHOÁ mà Agent (chạy tại xưởng) poll định kỳ.
    KHÔNG BAO GIỜ ném lỗi ra ngoài — tuyệt đối không làm hỏng vòng poll của agent."""
    try:
        from pha.views import _client_ip
        ip = _client_ip(request)
        if not is_public_ip(ip):             # IPv6 / nội bộ / rác / reserved -> KHÔNG BAO GIỜ ghi
            return
        now = timezone.now()
        if _get(K_SEEN) != ip:               # chỉ ghi khi ĐỔI (agent poll 3s/lần)
            _S().set(K_SEEN, ip)
            _S().set(K_SEEN_AT, now.isoformat())
        last = _parse(_get(K_SEEN_LAST))     # nhịp tim, throttle -> tối đa 1 ghi/phút
        if last is None or (now - last).total_seconds() >= _HEARTBEAT_S:
            _S().set(K_SEEN_LAST, now.isoformat())
        if is_on():
            set_workshop_ip(ip)
    except Exception:
        pass


def agent_online(max_age_s=AGENT_ONLINE_S):
    """Agent có đang báo về không? (dựa nhịp tim, KHÔNG dựa K_SEEN_AT — cái đó chỉ ghi
    khi IP ĐỔI, nên agent chạy ổn định nhiều ngày vẫn có K_SEEN_AT cũ mèm.)"""
    d = _parse(_get(K_SEEN_LAST))
    return bool(d) and (timezone.now() - d).total_seconds() <= max_age_s


# ---------------- API cho trang Quản lý chấm công (chỉ quản lý) ----------------
def _staff(request):
    return bool(getattr(request.user, 'is_staff', False))


def _fmt(iso):
    """ISO -> 'dd/mm HH:MM' giờ VN. (Lưu UTC, PHẢI localtime trước khi format.)"""
    d = _parse(iso)
    return timezone.localtime(d).strftime('%d/%m %H:%M') if d else ''


def _status_payload(request):
    from pha.views import _client_ip, _ip_private
    cur = _client_ip(request)
    prev, prev_at = _get(K_PREV), _get(K_PREV_AT)
    grace_left = ''
    d = _parse(prev_at)
    if prev and d:
        left = timedelta(hours=GRACE_HOURS) - (timezone.now() - d)
        if left.total_seconds() > 0:
            grace_left = '%dh%02d' % (left.seconds // 3600 + left.days * 24, (left.seconds % 3600) // 60)
    cur_v4 = True
    try:
        cur_v4 = ipaddress.ip_address(cur.strip()).version == 4 if cur else True
    except ValueError:
        cur_v4 = True
    return {
        'ok': True, 'on': is_on(),
        'auto': _get(K_AUTO), 'auto_at': _fmt(_get(K_AUTO_AT)),
        'seen': _get(K_SEEN), 'seen_at': _fmt(_get(K_SEEN_AT)),
        'agent_online': agent_online(), 'seen_last': _fmt(_get(K_SEEN_LAST)),
        'prev': prev if grace_left else '', 'grace_left': grace_left,
        'cur_ip': cur, 'cur_private': _ip_private(cur), 'cur_is_v4': cur_v4,
        'match': bool(_get(K_SEEN)) and _get(K_SEEN) == _get(K_AUTO),
        'grace_hours': GRACE_HOURS,
    }


def wifi_ip_status(request):
    """JSON trạng thái IP xưởng (trang Quản lý chấm công poll)."""
    if not _staff(request):
        return HttpResponseForbidden('staff only')
    return JsonResponse(_status_payload(request))


def wifi_ip_set(request):
    """Nút 'Dùng IP hiện tại' — đặt IP đang truy cập làm IP xưởng (dự phòng khi agent tắt)."""
    if not _staff(request):
        return HttpResponseForbidden('staff only')
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'msg': 'Cần POST.'}, status=405)
    from pha.views import _client_ip
    ip = _client_ip(request)
    if not is_public_ip(ip):
        return JsonResponse({'ok': False, 'msg': 'IP hiện tại (%s) không phải IP công cộng — '
                                                 'nginx chưa chuyển IP thật, không đặt được.'
                                                 % (ip or 'trống')})
    changed = set_workshop_ip(ip)
    return JsonResponse({'ok': True, 'ip': ip, 'changed': changed,
                         'msg': ('Đã đặt IP WiFi xưởng = %s' % ip) if changed
                                else ('IP xưởng vốn đã là %s' % ip)})


def wifi_ip_toggle(request):
    """Bật/tắt cho agent in tự ghi IP xưởng. BẬT chỉ khi agent ĐANG online — nếu không sẽ
    áp nhầm IP cũ mèm (vd IP ghi lúc test máy in ở nhà/4G) rồi đẩy IP đúng vào ân hạn."""
    if not _staff(request):
        return HttpResponseForbidden('staff only')
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'msg': 'Cần POST.'}, status=405)
    if request.POST.get('on') != '1':
        _S().set(K_ON, '0')
        return JsonResponse({'ok': True, 'on': False, 'msg': 'Đã tắt tự cập nhật IP.'})
    if not agent_online():
        return JsonResponse({'ok': False, 'on': False,
                             'msg': 'Agent in KHÔNG online (chưa báo về trong %d phút). Bật DALI Print '
                                    'Agent trên máy in rồi thử lại — hoặc bấm "Dùng IP hiện tại" khi bạn '
                                    'đang đứng ở xưởng.' % (AGENT_ONLINE_S // 60)})
    _S().set(K_ON, '1')
    set_workshop_ip(_get(K_SEEN))        # agent đang sống -> SEEN là IP xưởng thật, áp ngay
    return JsonResponse({'ok': True, 'on': True, 'msg': 'Đã bật. IP xưởng = %s' % _get(K_AUTO)})


def wifi_ip_clear(request):
    """Thoát hiểm khi lỡ đặt nhầm IP làm nhân viên bị chặn.
    PHẢI tắt luôn AUTO_ON — nếu không, agent sẽ ghi lại đúng cái IP sai đó sau ~3 giây."""
    if not _staff(request):
        return HttpResponseForbidden('staff only')
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'msg': 'Cần POST.'}, status=405)
    _S().set(K_ON, '0')                  # TẮT trước, không thì poll kế tiếp ghi lại ngay
    for k in (K_AUTO, K_AUTO_AT, K_PREV, K_PREV_AT, K_SEEN, K_SEEN_AT, K_SEEN_LAST):
        _S().set(k, '')
    return JsonResponse({'ok': True, 'msg': 'Đã TẮT tự cập nhật + xoá IP xưởng. '
                                            'Chấm công giờ chỉ theo danh sách IP nhập tay.'})
