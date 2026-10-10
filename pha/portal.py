# -*- coding: utf-8 -*-
"""TRUNG TÂM ĐIỀU HÀNH (portal kiểu MISA) — 1 lưới dẫn tới MỌI app/module của xưởng.

Phase 1 của "web tổng": chỉ là LAUNCHER (các module vốn đã chung 1 database = đã là
backend của nhau). Phase 2 (SSO) + Phase 3 (nối dữ liệu sâu) làm sau.

Đặt ở module riêng (không đụng views.py — user hay sửa song song). Thêm/bớt app chỉ cần
sửa CATALOG ở dưới. Cờ mỗi app: staff (chỉ quản lý thấy), ext (mở tab mới, link ra ngoài),
dev (đang phát triển — không bấm được), badge ('low'/'pour'/'rip' -> số liệu sống).
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

CATALOG = [
    {'title': 'Pha màu & Kho sơn', 'icon': 'bi-droplet-half', 'accent': 'green', 'apps': [
        {'name': 'Công thức pha màu', 'desc': 'Tra & lưu công thức pha', 'url': '/', 'icon': 'bi-palette'},
        {'name': 'Kho sơn', 'desc': 'Tồn kho · cảnh báo sắp hết', 'url': '/kho-son', 'icon': 'bi-bucket', 'badge': 'low', 'staff': True},
        {'name': 'Giao & thống kê rót', 'desc': 'Giao việc rót cho thợ', 'url': '/ma-tranh', 'icon': 'bi-send-check', 'badge': 'pour', 'staff': True},
        {'name': 'App thợ (pha + rót)', 'desc': 'Màn hình điện thoại', 'url': '/app', 'icon': 'bi-phone'},
        {'name': 'Lịch sử rót', 'desc': 'Ai rót gì, lúc nào', 'url': '/lich-su-rot', 'icon': 'bi-clock-history', 'staff': True},
    ]},
    {'title': 'Xử lý ảnh & Thiết kế AI', 'icon': 'bi-image', 'accent': 'blue', 'apps': [
        {'name': 'Tạo bản đồ màu', 'desc': 'Ảnh → tranh tô số', 'url': '/xu-ly-anh', 'icon': 'bi-card-image'},
        {'name': 'Đánh số ảnh phẳng', 'desc': 'Đánh số lại bản vẽ', 'url': '/anh-phang', 'icon': 'bi-123'},
        {'name': 'Kho mã tranh', 'desc': 'Mở lại sửa màu', 'url': '/kho-ma-tranh', 'icon': 'bi-bookmark-star'},
        {'name': 'Xưởng ảnh sản phẩm', 'desc': 'Đóng khung · đăng web', 'url': '/xuong-anh', 'icon': 'bi-easel'},
        {'name': 'Bảng màu DALI', 'desc': 'Danh mục màu chuẩn', 'url': '/dali-colors', 'icon': 'bi-collection'},
        {'name': 'Kho mẫu AI', 'desc': 'Ảnh mẫu tăng cường', 'url': '/kho-mau', 'icon': 'bi-images'},
        {'name': 'Thử mức độ AI', 'desc': 'So sánh các mức AI', 'url': '/anh-ai-test', 'icon': 'bi-stars'},
        {'name': 'Cài đặt AI', 'desc': 'API key Gemini/OpenAI', 'url': '/cai-dat-ai', 'icon': 'bi-key', 'staff': True},
    ]},
    {'title': 'Sản xuất & In ấn', 'icon': 'bi-printer', 'accent': 'indigo', 'apps': [
        {'name': 'Ghép khổ in', 'desc': 'Xếp khít vải · RIP Flexi', 'url': '/ghep-in', 'icon': 'bi-grid-3x3-gap', 'badge': 'rip', 'staff': True},
        {'name': 'In A3 in sẵn', 'desc': 'Bản đồ số → PDF A3', 'url': '/in-a3', 'icon': 'bi-file-earmark-pdf'},
        {'name': 'Khổ lớn siêu chi tiết', 'desc': 'Tranh 1×2m · 60 màu', 'url': '/kho-lon', 'icon': 'bi-aspect-ratio'},
        {'name': 'Sản xuất', 'desc': 'Theo dõi sản lượng', 'url': '/san-xuat', 'icon': 'bi-box2-heart', 'staff': True},
        {'name': 'Tranh hỏng', 'desc': 'Ghi nhận tranh lỗi', 'url': '/tranh-hong', 'icon': 'bi-x-octagon'},
    ]},
    {'title': 'Nhân sự', 'icon': 'bi-people', 'accent': 'teal', 'apps': [
        {'name': 'Chấm công', 'desc': 'Vào/ra theo WiFi xưởng', 'url': '/cham-cong', 'icon': 'bi-fingerprint'},
        {'name': 'Quản lý chấm công', 'desc': 'Bảng công · lương · IP', 'url': '/cham-cong-quan-ly', 'icon': 'bi-shield-check', 'staff': True},
        {'name': 'Nhân viên', 'desc': 'Tài khoản & phân quyền', 'url': '/nhan-vien', 'icon': 'bi-person-badge', 'staff': True},
        {'name': 'Nghỉ phép', 'desc': 'Xin & duyệt nghỉ', 'url': '/nghi-phep', 'icon': 'bi-calendar-x'},
        {'name': 'Năng suất', 'desc': 'Sản lượng theo người', 'url': '/nang-suat', 'icon': 'bi-graph-up-arrow'},
        {'name': 'Thi đua', 'desc': 'Bảng xếp hạng', 'url': '/thi-dua', 'icon': 'bi-trophy'},
    ]},
    {'title': 'Kế toán & Tài chính', 'icon': 'bi-cash-coin', 'accent': 'amber', 'apps': [
        {'name': 'Bảng điều khiển', 'desc': 'Chi phí · tồn · dự báo (tranh)', 'url': '/dashboard', 'icon': 'bi-speedometer2', 'staff': True},
        {'name': 'Lợi nhuận', 'desc': 'Lãi/lỗ theo kỳ (tranh)', 'url': '/loi-nhuan', 'icon': 'bi-cash-stack', 'staff': True},
        {'name': 'Thống kê', 'desc': 'Báo cáo tổng hợp (tranh)', 'url': '/thong-ke', 'icon': 'bi-bar-chart-line', 'staff': True},
        # 3 SỔ KẾ TOÁN RIÊNG theo mảng kinh doanh (tranh / bóng bay / 3D) — màu riêng từng mảng
        {'name': 'Kế toán Tranh', 'desc': 'ketoan.tranhdali.vn ↗', 'url': 'https://ketoan.tranhdali.vn', 'icon': 'bi-calculator', 'ext': True, 'staff': True, 'accent': 'green'},
        {'name': 'Kế toán Bóng bay', 'desc': 'Sổ sách mảng bóng bay', 'url': '#', 'icon': 'bi-calculator', 'dev': True, 'tag': 'chờ link', 'staff': True, 'accent': 'red'},
        {'name': 'Kế toán In 3D', 'desc': 'Sổ sách mảng in 3D', 'url': '#', 'icon': 'bi-calculator', 'dev': True, 'tag': 'chờ link', 'staff': True, 'accent': 'blue'},
    ]},
    {'title': 'Bán hàng', 'icon': 'bi-shop', 'accent': 'red', 'apps': [
        {'name': 'Web bán Tranh', 'desc': 'tranhdali.vn ↗', 'url': 'https://tranhdali.vn', 'icon': 'bi-globe2', 'ext': True, 'accent': 'green'},
        {'name': 'Web bán Bóng bay', 'desc': 'Website mảng bóng bay', 'url': '#', 'icon': 'bi-globe2', 'dev': True, 'tag': 'chờ link', 'accent': 'red'},
        {'name': 'Web In 3D', 'desc': 'Website mảng in 3D', 'url': '#', 'icon': 'bi-globe2', 'dev': True, 'tag': 'chờ link', 'accent': 'blue'},
    ]},
    {'title': 'Mảng kinh doanh khác', 'icon': 'bi-grid-1x2', 'accent': 'slate', 'apps': [
        {'name': 'Xưởng in 3D', 'desc': 'Máy Bambu · file in', 'url': '/quan-ly-in', 'icon': 'bi-box', 'staff': True},
        {'name': 'Soạn TKB nút học', 'desc': 'Nút môn học · kho nút', 'url': '/soan-tkb', 'icon': 'bi-calendar3'},
        {'name': 'Ghép-in thương mại', 'desc': 'Dịch vụ cho xưởng khác', 'url': '#', 'icon': 'bi-briefcase', 'dev': True, 'staff': True},
    ]},
    {'title': 'Quản lý & Hệ thống', 'icon': 'bi-gear-wide-connected', 'accent': 'slate', 'apps': [
        {'name': 'Trung tâm quản lý', 'desc': 'Tổng hợp điều hành', 'url': '/quan-ly', 'icon': 'bi-sliders', 'staff': True},
        {'name': 'Chuông nhắc giờ', 'desc': 'Nhắc giờ · thông báo', 'url': '/chuong', 'icon': 'bi-bell', 'staff': True},
        {'name': 'Hộp thư', 'desc': 'Thông báo nội bộ', 'url': '/inbox', 'icon': 'bi-inbox', 'staff': True},
        {'name': 'Sao lưu dữ liệu', 'desc': 'Backup & tải về', 'url': '/sao-luu', 'icon': 'bi-cloud-arrow-down', 'staff': True},
    ]},
]


def _live_badges(is_staff):
    """Số liệu sống cho badge trên tile. An toàn: lỗi -> 0."""
    low = pour = rip = 0
    if not is_staff:
        return {}
    try:
        from pha.models import PaintStock
        low = sum(1 for p in PaintStock.objects.all()
                  if getattr(p, 'low_threshold', 0) and p.stock <= p.low_threshold)
    except Exception:
        low = 0
    try:
        from pha.models import PourRequest
        pour = PourRequest.objects.filter(status=PourRequest.STATUS_PENDING).count()
    except Exception:
        pour = 0
    try:
        from pha.models import PrintJob
        rip = PrintJob.objects.filter(status=PrintJob.PENDING).count()
    except Exception:
        rip = 0
    return {
        'low': (low, '%d sắp hết' % low, 'text-bg-danger'),
        'pour': (pour, '%d chờ rót' % pour, 'text-bg-warning'),
        'rip': (rip, '%d chờ RIP' % rip, 'text-bg-primary'),
    }


@login_required(login_url='/login')
def trung_tam(request):
    """Lưới portal. Lọc tile theo quyền (staff), gắn badge số liệu sống."""
    is_staff = bool(getattr(request.user, 'is_staff', False))
    badges = _live_badges(is_staff)
    cats, total = [], 0
    for cat in CATALOG:
        apps = []
        for a in cat['apps']:
            if a.get('staff') and not is_staff:
                continue
            a2 = dict(a)
            bk = a.get('badge')
            b = badges.get(bk) if bk else None
            if b and b[0] > 0:
                a2['badge_text'], a2['badge_cls'] = b[1], b[2]
            apps.append(a2)
            total += 1
        if apps:
            cats.append({'title': cat['title'], 'icon': cat['icon'],
                         'accent': cat['accent'], 'apps': apps})
    return render(request, 'trung_tam.html',
                  {'cats': cats, 'total': total, 'is_staff': is_staff})
