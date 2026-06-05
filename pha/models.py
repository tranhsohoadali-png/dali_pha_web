from django.db import models


class ProductionLog(models.Model):
    """Nhật ký pha màu (thống kê lượng màu gốc dùng theo ngày/tháng)."""
    created_time = models.DateTimeField(auto_now_add=True)
    day = models.CharField(max_length=10, db_index=True)    # YYYY-MM-DD (giờ VN)
    month = models.CharField(max_length=7, db_index=True)   # YYYY-MM
    dali = models.CharField(max_length=100)
    hex = models.CharField(max_length=10, blank=True, default='')
    multiplier = models.FloatField(default=1)
    components = models.JSONField(default=list)
    total = models.FloatField(default=0)

    def __str__(self):
        return f'{self.day} {self.dali} x{self.multiplier}'
