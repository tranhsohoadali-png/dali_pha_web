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


class TrainingSample(models.Model):
    """KHO HỌC (Giai đoạn A): lưu lại các ca xử lý ảnh ĐÃ DUYỆT (đẹp) để hệ thống
    học dần — ảnh gốc + thông số đã dùng + bản thiết kế duyệt + bảng mã màu, kèm
    'sig' (chữ ký 8x8 RGB của ẢNH GỐC) để về sau tìm các ca CŨ giống ảnh mới mà
    gợi lại thông số/ảnh mẫu (Giai đoạn B). KHÔNG bị prune như ImageResult.
    File lưu trong MEDIA_ROOT/training_data/."""
    created_time = models.DateTimeField(auto_now_add=True)
    source_name = models.TextField()                          # ảnh gốc (training_data/...)
    enhanced_name = models.TextField(blank=True, default='')  # ảnh AI (nếu có)
    design_name = models.TextField(blank=True, default='')    # bản thiết kế đã duyệt
    result_name = models.TextField(blank=True, default='')    # bản đồ số
    params = models.JSONField(default=dict, blank=True)       # thông số đã dùng (preset, color_limit, smooth...)
    colors = models.JSONField(default=list, blank=True)       # bảng mã màu [[stt,hex,dali,percent],...]
    sig = models.JSONField(default=list, blank=True)          # chữ ký 8x8 RGB của ẢNH GỐC (so độ giống)
    note = models.CharField(max_length=200, blank=True, default='')
    user = models.CharField(max_length=80, blank=True, default='')

    class Meta:
        ordering = ['-created_time', '-id']

    def __str__(self):
        return f'{self.id}-{self.source_name}'


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


class Painting(models.Model):
    """Mã tranh: danh mục tranh tô màu số. Mỗi mã tranh gồm danh sách mã màu DALI
    cần rót. Chủ/quản lý khai báo trước; nhân viên chọn mã tranh là ra sẵn list màu."""
    code = models.CharField(max_length=100, unique=True)            # mã tranh
    name = models.CharField(max_length=200, blank=True, default='')  # tên tranh (không dùng nữa)
    colors = models.JSONField(default=list)        # (cũ) danh sách mã màu — không dùng nữa
    color_count = models.IntegerField(default=0)   # số màu của tranh (tự đếm từ ảnh / sửa tay)
    image = models.TextField(blank=True, default='')   # ảnh mẫu/bản đồ màu (file trong MEDIA_ROOT)
    note = models.CharField(max_length=200, blank=True, default='')
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated', '-id']             # mới lưu/cập nhật lên đầu

    def __str__(self):
        return self.code


class PourLog(models.Model):
    """Nhật ký RÓT MÀU cho từng mã tranh (thống kê số mã tranh + lượt từng màu)."""
    created_time = models.DateTimeField(auto_now_add=True)
    day = models.CharField(max_length=10, db_index=True)    # YYYY-MM-DD (giờ VN)
    month = models.CharField(max_length=7, db_index=True)   # YYYY-MM
    painting = models.CharField(max_length=100)             # mã tranh đã rót
    size = models.CharField(max_length=20, blank=True, default='', db_index=True)  # kích thước (vd 40x50)
    colors = models.JSONField(default=list)                 # màu đã rót [{'dali','hex'}]
    color_count = models.IntegerField(default=0)            # số mã màu trong lượt này
    qty = models.IntegerField(default=1)                    # số lượng tranh trong lượt rót
    user = models.CharField(max_length=80, blank=True, default='')   # ai đã rót
    request_id = models.IntegerField(null=True, blank=True)  # liên kết yêu cầu (nếu rót theo giao việc)

    def __str__(self):
        return f'{self.day} {self.painting} ×{self.qty}'


class PaintingProduction(models.Model):
    """Nhật ký SẢN XUẤT tranh (số lượng tranh thành phẩm) — quản lý TỰ ĐIỀN.
    Tách riêng khỏi luồng rót màu; thống kê số lượng tranh theo kích thước/mã/ngày."""
    created_time = models.DateTimeField(auto_now_add=True)
    day = models.CharField(max_length=10, db_index=True)    # YYYY-MM-DD (giờ VN)
    month = models.CharField(max_length=7, db_index=True)   # YYYY-MM
    painting = models.CharField(max_length=100, blank=True, default='')  # mã tranh (tuỳ chọn)
    size = models.CharField(max_length=20, blank=True, default='', db_index=True)  # kích thước
    qty = models.IntegerField(default=1)                    # số lượng tranh
    note = models.CharField(max_length=300, blank=True, default='')
    user = models.CharField(max_length=80, blank=True, default='')

    class Meta:
        ordering = ['-created_time', '-id']

    def __str__(self):
        return f'{self.day} {self.painting} {self.size} ×{self.qty}'


class Attendance(models.Model):
    """Chấm công: mỗi nhân viên 1 dòng/ngày (giờ vào, giờ ra). Chỉ chấp nhận khi IP
    mạng trùng Wifi công ty (cấu hình ở AppSetting 'ATTENDANCE_IPS')."""
    user = models.CharField(max_length=80, db_index=True)
    day = models.CharField(max_length=10, db_index=True)    # YYYY-MM-DD (giờ VN)
    month = models.CharField(max_length=7, db_index=True)   # YYYY-MM
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    ip_in = models.CharField(max_length=64, blank=True, default='')
    ip_out = models.CharField(max_length=64, blank=True, default='')
    device_in = models.CharField(max_length=64, blank=True, default='')   # mã thiết bị lúc vào
    device_out = models.CharField(max_length=64, blank=True, default='')  # mã thiết bị lúc ra

    class Meta:
        unique_together = ('user', 'day')
        ordering = ['-day', 'user']

    def __str__(self):
        return f'{self.day} {self.user}'


class DeviceBind(models.Model):
    """Khoá thiết bị: mỗi tài khoản gắn 1 máy (1-1) để chống chấm công hộ."""
    username = models.CharField(max_length=80, unique=True)
    token = models.CharField(max_length=64, db_index=True)
    bound_time = models.DateTimeField(auto_now=True)
    user_agent = models.CharField(max_length=200, blank=True, default='')

    def __str__(self):
        return f'{self.username} · {self.token[:10]}'


class PushSubscription(models.Model):
    """Đăng ký Web Push của trình duyệt nhân viên (để đẩy thông báo cả khi tắt app)."""
    username = models.CharField(max_length=80, db_index=True)
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.username} · {self.endpoint[:40]}'


class PourRequest(models.Model):
    """Yêu cầu rót màu do QUẢN LÝ giao cho nhân viên (một chiều). Nhân viên nhận
    và đánh dấu 'đã rót' để tắt yêu cầu."""
    STATUS_PENDING = 'pending'
    STATUS_DONE = 'done'

    created_time = models.DateTimeField(auto_now_add=True)
    painting = models.CharField(max_length=100)            # mã tranh cần rót
    size = models.CharField(max_length=20, blank=True, default='')  # kích thước (vd 40x50)
    colors = models.JSONField(default=list)               # mã màu cần rót [{'dali','hex'}]
    qty = models.IntegerField(default=1)                  # số lượng tranh
    note = models.CharField(max_length=300, blank=True, default='')
    assignee = models.CharField(max_length=80, blank=True, default='')  # giao cho ai ('' = mọi người)
    created_by = models.CharField(max_length=80, blank=True, default='')
    status = models.CharField(max_length=20, default=STATUS_PENDING, db_index=True)
    done_by = models.CharField(max_length=80, blank=True, default='')
    done_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['status', '-created_time', '-id']      # đang chờ lên đầu

    def __str__(self):
        return f'{self.painting} → {self.assignee or "mọi người"} ({self.status})'
