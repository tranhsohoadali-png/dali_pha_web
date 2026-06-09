"""Context processor cung cấp dữ liệu cho thanh header dùng chung (_nav.html)."""
from pha.models import PaintStock, PourRequest


def nav(request):
    """Badge cho menu: số màu sơn sắp hết + số yêu cầu rót màu đang chờ."""
    count = 0
    pour_pending = 0
    try:
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and user.is_staff:
            for p in PaintStock.objects.all():
                if p.low_threshold and p.stock <= p.low_threshold:
                    count += 1
            pour_pending = PourRequest.objects.filter(
                status=PourRequest.STATUS_PENDING).count()
    except Exception:
        count = 0
        pour_pending = 0
    return {'nav_low_count': count, 'nav_pour_pending': pour_pending}
