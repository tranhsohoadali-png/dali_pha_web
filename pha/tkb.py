# -*- coding: utf-8 -*-
"""PHIẾU SOẠN NÚT MÔN HỌC (Thời khóa biểu) — trang tích khi soạn đơn để không thiếu nút.

Trang TĨNH (checklist chạy hoàn toàn ở trình duyệt, tự lưu bằng localStorage) nên KHÔNG
cần model/migration/endpoint dữ liệu. Đặt ở module riêng để khỏi đụng views.py.
"""
from django.shortcuts import render

from pha.views import staff_required


@staff_required
def soan_tkb(request):
    return render(request, 'soan_tkb.html')
