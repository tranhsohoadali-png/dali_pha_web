from django.db import models


class Recipe(models.Model):
    """Công thức pha (lưu trong DB để an toàn khi nhiều người sửa cùng lúc)."""
    dali = models.CharField(max_length=100, unique=True)
    hex = models.CharField(max_length=10, blank=True, default='')
    components = models.JSONField(default=list)   # [{name, grams}]
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated', '-id']            # mới lưu/cập nhật lên đầu

    def __str__(self):
        return self.dali


class ImageResult(models.Model):
    """Ảnh đã xử lý: bản đồ màu đánh số + bảng màu DALI."""
    STATUS_PROCESSING = 'processing'
    STATUS_DONE = 'done'
    STATUS_ERROR = 'error'

    created_time = models.DateTimeField(auto_now_add=True)
    name = models.TextField()                 # tên file ảnh gốc (có timestamp)
    name_output = models.TextField(blank=True, default='')   # file kết quả png
    colors = models.JSONField(default=list, blank=True)      # [[stt,hex,dali,percent],...]
    status = models.CharField(max_length=20, default=STATUS_PROCESSING)
    error_message = models.TextField(blank=True, default='')
    user = models.CharField(max_length=80, blank=True, default='')

    def __str__(self):
        return f'{self.id}-{self.name}'


class PaintStock(models.Model):
    """Tồn kho từng màu sơn gốc (gram). Mỗi lần pha tự trừ."""
    name = models.CharField(max_length=100, unique=True)
    stock = models.FloatField(default=0)            # tồn kho hiện tại (g)
    low_threshold = models.FloatField(default=0)    # ngưỡng cảnh báo sắp hết (g)

    def __str__(self):
        return f'{self.name}: {self.stock}g'


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
    user = models.CharField(max_length=80, blank=True, default='')   # ai đã pha

    def __str__(self):
        return f'{self.day} {self.dali} x{self.multiplier}'
