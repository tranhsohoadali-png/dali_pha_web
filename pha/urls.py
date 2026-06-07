from django.conf import settings
from django.urls import path, re_path
from django.views.static import serve as static_serve

from pha import views

urlpatterns = [
    path('login', views.login_view, name='login'),
    path('logout', views.logout_view, name='logout'),
    path('nhan-vien', views.nhan_vien, name='nhan_vien'),
    path('kho-son', views.kho_son, name='kho_son'),
    path('dashboard', views.dashboard, name='dashboard'),
    path('quan-ly', views.quan_ly, name='quan_ly'),
    path('quan-ly-nhap', views.quan_ly_nhap, name='quan_ly_nhap'),
    path('manifest-ql.webmanifest', views.manifest_ql, name='manifest_ql'),
    path('', views.cong_thuc_mau, name='home'),
    path('cong-thuc-mau', views.cong_thuc_mau, name='cong_thuc_mau'),
    path('app', views.mobile, name='mobile'),
    path('pha', views.pha, name='pha'),
    path('thong-ke', views.thong_ke, name='thong_ke'),
    path('lich-su', views.lich_su, name='lich_su'),
    path('thong-ke-excel', views.export_thong_ke_excel, name='thong_ke_excel'),

    # Xử lý ảnh (tab cho chủ)
    path('dali-colors', views.dali_colors, name='dali_colors'),
    path('kho-mau', views.kho_mau, name='kho_mau'),
    path('xu-ly-anh', views.xu_ly_anh, name='xu_ly_anh'),
    path('anh-result', views.anh_result, name='anh_result'),
    path('anh-save-color', views.anh_save_color, name='anh_save_color'),
    path('anh-export-colors', views.anh_export_colors, name='anh_export_colors'),
    path('anh-export-xlsx', views.anh_export_xlsx, name='anh_export_xlsx'),
    path('anh-legend', views.anh_legend, name='anh_legend'),
    path('anh-download', views.anh_download_result, name='anh_download'),

    path('manifest.webmanifest', views.manifest, name='manifest'),
    path('sw.js', views.service_worker, name='sw'),

    # Phục vụ file media (icon PWA + ảnh kết quả). Tên file có timestamp nên khó đoán.
    re_path(r'^media/(?P<path>.+)$', static_serve, {'document_root': settings.MEDIA_ROOT}),
]
