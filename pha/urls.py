from django.conf import settings
from django.urls import path, re_path
from django.views.static import serve as static_serve

from pha import views
from pha import face_api
from pha import learn_lib

urlpatterns = [
    path('login', views.login_view, name='login'),
    path('logout', views.logout_view, name='logout'),
    path('home', views.home, name='home_app'),
    path('manifest-app.webmanifest', views.manifest_app, name='manifest_app'),
    path('nhan-vien', views.nhan_vien, name='nhan_vien'),
    path('kho-son', views.kho_son, name='kho_son'),
    path('dashboard', views.dashboard, name='dashboard'),
    path('quan-ly', views.quan_ly, name='quan_ly'),
    path('quan-ly-nhap', views.quan_ly_nhap, name='quan_ly_nhap'),
    path('manifest-ql.webmanifest', views.manifest_ql, name='manifest_ql'),

    # API cho phần mềm kế toán (ketoan.tranhdali.vn)
    path('api/ketoan', views.api_ketoan, name='api_ketoan'),
    path('api/luong', views.api_luong, name='api_luong'),

    path('', views.cong_thuc_mau, name='home'),
    path('cong-thuc-mau', views.cong_thuc_mau, name='cong_thuc_mau'),
    path('app', views.mobile, name='mobile'),
    path('pha', views.pha, name='pha'),
    path('thong-ke', views.thong_ke, name='thong_ke'),
    path('lich-su', views.lich_su, name='lich_su'),
    path('thong-ke-excel', views.export_thong_ke_excel, name='thong_ke_excel'),

    # Rót màu theo mã tranh
    path('ma-tranh', views.ma_tranh, name='ma_tranh'),
    path('ma-tranh-doc-so-mau', views.doc_so_mau, name='doc_so_mau'),
    path('ma-tranh-cap-nhat-so-mau', views.cap_nhat_so_mau, name='cap_nhat_so_mau'),
    path('app-rot', views.rot_mau_app, name='rot_mau_app'),
    path('rot', views.rot, name='rot'),
    path('rot-yeu-cau', views.rot_yeu_cau_list, name='rot_yeu_cau_list'),
    path('lich-su-rot', views.lich_su_rot, name='lich_su_rot'),
    path('xoa-lich-su-rot', views.xoa_lich_su_rot, name='xoa_lich_su_rot'),

    # Sản xuất tranh (module quản lý tự điền)
    path('san-xuat', views.san_xuat, name='san_xuat'),
    path('thong-ke-san-xuat', views.thong_ke_san_xuat, name='thong_ke_san_xuat'),
    path('san-xuat-excel', views.export_san_xuat_excel, name='san_xuat_excel'),

    # Năng suất & lương khoán nhân viên
    path('nang-suat', views.nang_suat, name='nang_suat'),
    path('thong-ke-nang-suat', views.thong_ke_nang_suat, name='thong_ke_nang_suat'),
    path('nang-suat-excel', views.export_nang_suat_excel, name='nang_suat_excel'),

    # Doanh thu - lợi nhuận
    path('loi-nhuan', views.loi_nhuan, name='loi_nhuan'),
    path('thong-ke-loi-nhuan', views.thong_ke_loi_nhuan, name='thong_ke_loi_nhuan'),
    path('loi-nhuan-excel', views.export_loi_nhuan_excel, name='loi_nhuan_excel'),

    # Chấm công (theo IP Wifi công ty)
    path('cham-cong', views.cham_cong, name='cham_cong'),
    path('cham-cong-quan-ly', views.cham_cong_quan_ly, name='cham_cong_quan_ly'),
    path('cham-cong-ip', views.cham_cong_ip, name='cham_cong_ip'),
    path('cham-cong-excel', views.export_cham_cong_excel, name='cham_cong_excel'),
    path('quan-ly-giao-rot', views.quan_ly_giao_rot, name='quan_ly_giao_rot'),
    path('push-key', views.push_key, name='push_key'),
    path('push-subscribe', views.push_subscribe, name='push_subscribe'),
    path('thong-ke-rot', views.thong_ke_rot, name='thong_ke_rot'),
    path('thong-ke-rot-excel', views.export_thong_ke_rot_excel, name='thong_ke_rot_excel'),

    # Xử lý ảnh (tab cho chủ)
    path('dali-colors', views.dali_colors, name='dali_colors'),
    path('kho-mau', views.kho_mau, name='kho_mau'),
    path('cai-dat-ai', views.cai_dat_ai, name='cai_dat_ai'),
    path('xu-ly-anh', views.xu_ly_anh, name='xu_ly_anh'),
    path('anh-detect-face', face_api.anh_detect_face, name='anh_detect_face'),
    path('anh-result', views.anh_result, name='anh_result'),
    path('anh-preset', views.anh_preset, name='anh_preset'),
    path('anh-save-color', views.anh_save_color, name='anh_save_color'),
    path('anh-nearest-dali', views.anh_nearest_dali, name='anh_nearest_dali'),
    path('anh-export-colors', views.anh_export_colors, name='anh_export_colors'),
    path('anh-export-xlsx', views.anh_export_xlsx, name='anh_export_xlsx'),
    path('anh-legend', views.anh_legend, name='anh_legend'),
    path('anh-download', views.anh_download_result, name='anh_download'),

    # Kho học (Giai đoạn A): lưu ca xử lý đẹp để hệ thống học dần
    path('anh-luu-kho-hoc', learn_lib.save_sample, name='anh_luu_kho_hoc'),
    path('kho-hoc', learn_lib.kho_hoc, name='kho_hoc'),
    path('kho-hoc-xoa', learn_lib.delete_sample, name='kho_hoc_xoa'),

    path('manifest.webmanifest', views.manifest, name='manifest'),
    path('sw.js', views.service_worker, name='sw'),

    # Phục vụ file media (icon PWA + ảnh kết quả). Tên file có timestamp nên khó đoán.
    re_path(r'^media/(?P<path>.+)$', static_serve, {'document_root': settings.MEDIA_ROOT}),
]
