"""Context processor cung cấp dữ liệu cho thanh header dùng chung (_nav.html)."""
from pha.models import PaintStock


def nav(request):
    """Đếm số màu sơn sắp hết để hiện badge trên menu 'Kho sơn' ở mọi trang."""
    count = 0
    try:
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated and user.is_staff:
            for p in PaintStock.objects.all():
                if p.low_threshold and p.stock <= p.low_threshold:
                    count += 1
    except Exception:
        count = 0
    return {'nav_low_count': count}
