from django.conf import settings
from django.urls import path, re_path
from django.views.static import serve as static_serve

from pha import views
from pha import learn_lib
from pha import flat_number
from pha import attend_nudge
from pha import ketoan_feed
from pha import backup_lib
from pha import extra_views
from pha import ai_levels
from pha import imposition
from pha import in_a3 as in_a3_views
from pha import large_format
from pha import rip_views
from pha import wifi_ip
from pha import product_studio
from pha import web_publish
from pha import kho_ma as kho_ma_views

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

    # API xử lý ảnh cho web bán hàng (tranhdali.vn/thiet-ke)
    path('api/xu-ly-anh', views.api_xu_ly_anh, name='api_xu_ly_anh'),
    path('api/xu-ly-anh-trang-thai', views.api_xu_ly_anh_status, name='api_xu_ly_anh_status'),

    # API cho phần mềm kế toán (ketoan.tranhdali.vn)
    path('api/ketoan', views.api_ketoan, name='api_ketoan'),
    path('api/luong', views.api_luong, name='api_luong'),
    path('api/nang-suat', ketoan_feed.api_nang_suat, name='api_nang_suat'),
    path('ketoan-luong-test', views.ketoan_luong_test, name='ketoan_luong_test'),
    path('nang-suat-day-ketoan', ketoan_feed.feed, name='nang_suat_day_ketoan'),

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
    path('cham-cong-nhac', attend_nudge.nudge, name='cham_cong_nhac'),
    path('nghi-phep', extra_views.nghi_phep, name='nghi_phep'),

    # Tranh hỏng (QC) + thi đua + chuông xưởng
    path('tranh-hong', extra_views.tranh_hong, name='tranh_hong'),
    path('thi-dua', extra_views.thi_dua, name='thi_dua'),
    path('chuong', extra_views.chuong, name='chuong'),
    path('chuong-config', extra_views.chuong_config, name='chuong_config'),

    # Ghép khổ in (imposition / nesting -> PDF cho Flexi)
    path('ghep-in', imposition.ghep_in, name='ghep_in'),
    # In A3: tải file -> đặt số lượng -> 1 PDF khổ A3 in sẵn
    path('in-a3', in_a3_views.in_a3, name='in_a3'),
    path('in-a3/upload', in_a3_views.in_a3_upload, name='in_a3_upload'),
    path('in-a3/xoa', in_a3_views.in_a3_xoa, name='in_a3_xoa'),
    path('in-a3/thumb', in_a3_views.in_a3_thumb, name='in_a3_thumb'),
    path('in-a3/pdf', in_a3_views.in_a3_pdf, name='in_a3_pdf'),
    # Khổ lớn: tranh tô số siêu chi tiết khổ lớn (1×2m, 60 màu) — chạy nền
    path('kho-lon', large_format.kho_lon, name='kho_lon'),
    path('kho-lon/upload', large_format.kho_lon_upload, name='kho_lon_upload'),
    path('kho-lon/status', large_format.kho_lon_status, name='kho_lon_status'),

    # XƯỞNG ẢNH SẢN PHẨM: đóng khung tranh vào mockup -> ảnh sản phẩm
    path('xuong-anh', product_studio.xuong_anh, name='xuong_anh'),
    path('xuong-anh/khung-upload', product_studio.khung_upload, name='xuong_anh_khung_upload'),
    path('xuong-anh/khung-xoa', product_studio.khung_xoa, name='xuong_anh_khung_xoa'),
    path('xuong-anh/ghep', product_studio.ghep, name='xuong_anh_ghep'),
    path('xuong-anh/out-xoa', product_studio.out_xoa, name='xuong_anh_out_xoa'),
    # Đăng sản phẩm lên tranhdali.vn (qua agent.tranhdali.vn — Basic auth)
    path('xuong-anh/dang-web/chuan-bi', web_publish.dang_web_chuan_bi, name='dang_web_chuan_bi'),
    path('xuong-anh/dang-web/draft', web_publish.dang_web_draft, name='dang_web_draft'),
    path('xuong-anh/dang-web/publish', web_publish.dang_web_publish, name='dang_web_publish'),
    path('xuong-anh/dang-web/danh-muc', web_publish.dang_web_danh_muc, name='dang_web_danh_muc'),
    path('xuong-anh/dang-web/tu-dong', web_publish.dang_web_tu_dong, name='dang_web_tu_dong'),
    path('xuong-anh/dang-web/cai-dat', web_publish.dang_web_cai_dat, name='dang_web_cai_dat'),
    # Hàng đợi RIP (web <-> DALI Print Agent <-> Flexi)
    path('api/rip-queue', rip_views.rip_queue, name='rip_queue'),
    # IP WiFi xưởng tự cập nhật (chấm công) — chỉ quản lý
    path('api/wifi-ip', wifi_ip.wifi_ip_status, name='wifi_ip_status'),
    path('api/wifi-ip/set', wifi_ip.wifi_ip_set, name='wifi_ip_set'),
    path('api/wifi-ip/toggle', wifi_ip.wifi_ip_toggle, name='wifi_ip_toggle'),
    path('api/wifi-ip/clear', wifi_ip.wifi_ip_clear, name='wifi_ip_clear'),
    path('api/rip-status', rip_views.rip_status, name='rip_status'),
    path('api/rip-list', rip_views.rip_list, name='rip_list'),
    path('api/rip-action', rip_views.rip_action, name='rip_action'),
    path('api/rip-stats', rip_views.rip_stats, name='rip_stats'),
    path('api/rip-cost', rip_views.rip_cost, name='rip_cost'),

    # Sao lưu dữ liệu
    path('sao-luu', backup_lib.backup_page, name='sao_luu'),
    path('sao-luu-chay', backup_lib.backup_run_view, name='sao_luu_chay'),
    path('sao-luu-tai', backup_lib.backup_download, name='sao_luu_tai'),
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
    # Thử "Mức độ AI" (nhẹ/vừa/mạnh) — module RIÊNG nội bộ, không đụng API bán hàng
    path('anh-ai-test', ai_levels.anh_ai_test, name='anh_ai_test'),
    path('anh-ai-test-run', ai_levels.anh_ai_test_run, name='anh_ai_test_run'),
    path('anh-ai-test-status', ai_levels.anh_ai_test_status, name='anh_ai_test_status'),
    # Đánh số ảnh phẳng (đã thiết kế) — TAB RIÊNG + API (module riêng, không AI)
    path('anh-phang', flat_number.anh_phang, name='anh_phang'),
    path('xu-ly-anh-phang', flat_number.xu_ly_anh_phang, name='xu_ly_anh_phang'),
    path('anh-result', views.anh_result, name='anh_result'),
    path('anh-preset', views.anh_preset, name='anh_preset'),
    path('anh-save-color', views.anh_save_color, name='anh_save_color'),
    path('anh-nearest-dali', views.anh_nearest_dali, name='anh_nearest_dali'),
    path('anh-export-colors', views.anh_export_colors, name='anh_export_colors'),
    path('anh-export-xlsx', views.anh_export_xlsx, name='anh_export_xlsx'),
    path('anh-legend', views.anh_legend, name='anh_legend'),
    path('anh-download', views.anh_download_result, name='anh_download'),

    # Kho học (Giai đoạn A): lưu ca xử lý đẹp để hệ thống học dần
    # KHO MÃ TRANH: lưu bản đã số hoá -> mở lại sửa màu (khỏi chạy lại AI)
    path('kho-ma-tranh', kho_ma_views.kho_ma, name='kho_ma_tranh'),
    path('kho-ma-tranh/luu', kho_ma_views.kho_ma_luu, name='kho_ma_luu'),
    path('kho-ma-tranh/mo', kho_ma_views.kho_ma_mo, name='kho_ma_mo'),
    path('kho-ma-tranh/xoa', kho_ma_views.kho_ma_xoa, name='kho_ma_xoa'),

    path('anh-luu-kho-hoc', learn_lib.save_sample, name='anh_luu_kho_hoc'),
    path('kho-hoc', learn_lib.kho_hoc, name='kho_hoc'),
    path('kho-hoc-xoa', learn_lib.delete_sample, name='kho_hoc_xoa'),

    path('manifest.webmanifest', views.manifest, name='manifest'),
    path('sw.js', views.service_worker, name='sw'),

    # Phục vụ file media (icon PWA + ảnh kết quả). Tên file có timestamp nên khó đoán.
    re_path(r'^media/(?P<path>.+)$', static_serve, {'document_root': settings.MEDIA_ROOT}),
]
