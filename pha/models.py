from django.db import models


class AppSetting(models.Model):
    """Cấu hình ứng dụng dạng key/value (vd: GOOGLE_API_KEY) — chỉnh được qua UI,
    lưu DB nên giữ nguyên sau khi khởi động lại máy chủ."""
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True, default='')
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key, default=''):
        obj = cls.objects.filter(key=key).first()
        return obj.value if obj else default

    @classmethod
    def set(cls, key, value):
        cls.objects.update_or_create(key=key, defaults={'value': value})


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
    enhanced_name = models.TextField(blank=True, default='')  # file ảnh đã tăng cường bằng AI (nếu có)
    design_name = models.TextField(blank=True, default='')   # file ảnh thiết kế (bản màu phẳng đã đơn giản hoá)
    name_output = models.TextField(blank=True, default='')   # file kết quả png
    colors = models.JSONField(default=list, blank=True)      # [[stt,hex,dali,percent],...]
    params = models.JSONField(default=dict, blank=True)      # thông số đầu vào: {enhance,color_limit,min_area}
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
    price_per_kg = models.FloatField(default=0)     # giá (đồng / kg)

    def __str__(self):
        return f'{self.name}: {self.stock}g'


class StyleSample(models.Model):
    """Mẫu thành phẩm (tranh tô màu số hoá đã hoàn thiện) dùng làm ảnh tham
    chiếu phong cách cho AI. Mỗi mẫu lưu kèm 'sig' (chữ ký màu/bố cục) để chọn
    nhanh các mẫu giống ảnh khách nhất khi tăng cường bằng AI."""
    created_time = models.DateTimeField(auto_now_add=True)
    name = models.TextField()                                  # file ảnh trong MEDIA_ROOT/style_samples
    category = models.CharField(max_length=60, blank=True, default='', db_index=True)
    sig = models.JSONField(default=list, blank=True)           # chữ ký 8x8 RGB (192 số) để so khớp
    note = models.CharField(max_length=200, blank=True, default='')
    user = models.CharField(max_length=80, blank=True, default='')

    class Meta:
        ordering = ['-created_time', '-id']

    def __str__(self):
        return f'{self.id}-{self.category}-{self.name}'


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
    cost = models.FloatField(default=0)            # chi phí sơn của mẻ này (đồng)

    def __str__(self):
        return f'{self.day} {self.dali} x{self.multiplier}'
