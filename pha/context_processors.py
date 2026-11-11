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
    # Badge "Giao việc" (việc trễ + đến hạn hôm nay/mai của chính người đang xem) — đọc JSON có cache
    nav_viec = 0
    try:
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            from pha.giaoviec import dem_viec
            dv = dem_viec(user.username)
            nav_viec = dv['tre'] + dv['han_mai']
    except Exception:
        nav_viec = 0
    return {'nav_low_count': count, 'nav_pour_pending': pour_pending, 'nav_viec': nav_viec}
