"""Nhắc việc GIAO VIỆC (push gộp mỗi người). Chạy tay hoặc cron trên VPS:

    cd /var/www/dali_pha_web && sudo -u www-data venv/bin/python manage.py giaoviec_nhac --dot sang
    ... --dot chieu          # 16:30: tổng hợp cho trưởng phòng + quản trị
    ... --thu                # chỉ XEM TRƯỚC (không gửi, không ghi sổ chống lặp)
    ... --force              # gửi cả ngày nghỉ / bỏ qua sổ chống lặp

KHUYẾN NGHỊ dùng lệnh này trong cron (`crontab -u www-data -e`, dòng mẫu ở /giao-viec -> Cài đặt)
thay cho curl /giao-viec/nhac?key=... (khoá trên URL có thể lộ ở access log).
(Chạy bằng www-data để file trong runtime/giaoviec giữ đúng chủ sở hữu.)
"""
import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Gửi nhắc việc (Giao việc): --dot sang|chieu [--thu] [--force]'

    def add_arguments(self, parser):
        parser.add_argument('--dot', choices=['sang', 'chieu'], default='sang')
        parser.add_argument('--thu', action='store_true', help='Chỉ xem trước, không gửi')
        parser.add_argument('--force', action='store_true', help='Gửi cả ngày nghỉ, bỏ qua sổ chống lặp')

    def handle(self, *args, **opts):
        from pha import giaoviec
        res = giaoviec.chay_nhac(opts['dot'], gui=not opts['thu'], force=opts['force'])
        self.stdout.write(json.dumps(res, ensure_ascii=False, indent=1))
