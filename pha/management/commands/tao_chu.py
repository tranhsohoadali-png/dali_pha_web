"""Tạo/cập nhật tài khoản CHỦ (quản lý). Dùng:  manage.py tao_chu <user> <password>"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Tạo tài khoản chủ (quyền quản lý). VD: manage.py tao_chu chu matkhau123'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('password')

    def handle(self, *args, **opts):
        u, created = User.objects.get_or_create(username=opts['username'])
        u.is_staff = True
        u.is_superuser = True
        u.set_password(opts['password'])
        u.save()
        self.stdout.write(('Da tao' if created else 'Da cap nhat') +
                          f" tai khoan chu: {opts['username']}")
