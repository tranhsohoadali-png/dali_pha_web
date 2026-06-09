"""
WEB PUSH — thông báo cho nhân viên CẢ KHI ĐÃ TẮT APP.

- Khoá VAPID được TỰ SINH một lần và lưu trong DB (AppSetting) -> không phải tạo tay.
- Cần thư viện `pywebpush` (kéo theo `cryptography`, `py_vapid`). Nếu THIẾU thư viện
  hoặc lỗi -> mọi hàm tự bỏ qua êm (app vẫn chạy, vẫn còn thông báo kiểu poll khi mở app).
"""
import base64
import json
import os

from django.conf import settings

VAPID_SUB = 'mailto:admin@tranhdali.vn'   # liên hệ gắn vào yêu cầu push (bắt buộc có)
_VAPID_FILE = os.path.join(str(settings.BASE_DIR), 'vapid_private.pem')


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def is_available():
    """True nếu máy chủ đã cài pywebpush (đủ để gửi web push)."""
    try:
        import pywebpush  # noqa: F401
        return True
    except Exception:
        return False


def get_vapid():
    """Trả (private_pem, public_b64). Tự sinh & lưu DB nếu chưa có. None nếu thiếu cryptography."""
    from pha.models import AppSetting
    priv = AppSetting.get('VAPID_PRIVATE_PEM')
    pub = AppSetting.get('VAPID_PUBLIC_B64')
    if priv and pub:
        return priv, pub
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
    except Exception:
        return None
    key = ec.generate_private_key(ec.SECP256R1())
    priv_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    raw_pub = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)
    pub_b64 = _b64url(raw_pub)
    AppSetting.set('VAPID_PRIVATE_PEM', priv_pem)
    AppSetting.set('VAPID_PUBLIC_B64', pub_b64)
    return priv_pem, pub_b64


def public_key():
    """Khoá công khai (applicationServerKey) cho trình duyệt đăng ký. '' nếu chưa sẵn sàng."""
    if not is_available():
        return ''
    v = get_vapid()
    return v[1] if v else ''


def _vapid_private_path():
    """Ghi private key ra file PEM (pywebpush nhận đường dẫn file - tương thích rộng)."""
    v = get_vapid()
    if not v:
        return None
    priv_pem = v[0]
    try:
        if not os.path.exists(_VAPID_FILE):
            with open(_VAPID_FILE, 'w', encoding='utf-8') as f:
                f.write(priv_pem)
    except OSError:
        return None
    return _VAPID_FILE


def _send_one(sub, payload_json):
    """Gửi 1 push. Xoá subscription nếu trình duyệt đã huỷ (404/410)."""
    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return
    path = _vapid_private_path()
    if not path:
        return
    try:
        webpush(
            subscription_info={
                'endpoint': sub.endpoint,
                'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
            },
            data=payload_json,
            vapid_private_key=path,
            vapid_claims={'sub': VAPID_SUB},
            timeout=10,
        )
    except WebPushException as e:
        st = getattr(getattr(e, 'response', None), 'status_code', None)
        if st in (404, 410):
            try:
                sub.delete()
            except Exception:
                pass
    except Exception:
        pass


def notify_pour(req):
    """Đẩy web push cho nhân viên khi quản lý giao 1 yêu cầu rót (req: PourRequest)."""
    if not is_available():
        return
    try:
        from pha.models import PushSubscription, Painting
        p = Painting.objects.filter(code__iexact=req.painting).first()
        cnt = p.color_count if p else 0
        body = f'{req.painting} ×{req.qty} · {cnt} màu' + (f' · {req.note}' if req.note else '')
        payload = json.dumps({
            'title': '🎨 Mã màu cần rót', 'body': body,
            'url': '/app-rot', 'tag': f'rot-{req.id}', 'icon': '/media/icon-192.png',
        })
        subs = PushSubscription.objects.all()
        if req.assignee:
            subs = subs.filter(username=req.assignee)        # giao đích danh
        elif req.created_by:
            subs = subs.exclude(username=req.created_by)      # "mọi người" (trừ người giao)
        for s in subs:
            _send_one(s, payload)
    except Exception:
        pass
