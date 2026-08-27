from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _sqlite_pragmas(sender, connection, **kwargs):
    """Bật WAL cho SQLite: READER KHÔNG bị chặn bởi WRITER (mặc định journal=DELETE thì
    writer chặn hết reader -> job xử-lý-ảnh nền ghi DB làm trang trắng). busy_timeout 30s
    + synchronous=NORMAL (an toàn với WAL, nhanh). Chạy 1 lần mỗi kết nối mới."""
    if connection.vendor != 'sqlite':
        return
    try:
        cur = connection.cursor()
        cur.execute('PRAGMA journal_mode=WAL;')
        cur.execute('PRAGMA synchronous=NORMAL;')
        cur.execute('PRAGMA busy_timeout=30000;')
        cur.execute('PRAGMA wal_autocheckpoint=1000;')
    except Exception:
        pass


class PhaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pha'

    def ready(self):
        # WAL cho SQLite (chống "database is locked" -> trắng màn hình khi có job nền)
        connection_created.connect(_sqlite_pragmas)
        # Tự đẩy năng suất sang kế toán (ketoan.tranhdali.vn) khi có log mới
        try:
            from pha import ketoan_feed
            ketoan_feed.connect_signals()
        except Exception:
            pass
