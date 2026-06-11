from django.apps import AppConfig


class PhaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pha'

    def ready(self):
        # Tự đẩy năng suất sang kế toán (ketoan.tranhdali.vn) khi có log mới
        try:
            from pha import ketoan_feed
            ketoan_feed.connect_signals()
        except Exception:
            pass
