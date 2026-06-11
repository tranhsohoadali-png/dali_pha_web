# Deploy "Công thức pha + App điện thoại" lên VPS (subdomain)

Gói này CHỈ gồm phần công thức pha + app điện thoại cho nhân viên.
Phụ thuộc rất nhẹ: **Django + numpy** (không có opencv/xử lý ảnh).

- Trang quản lý công thức (cho chủ):  `https://mau.tenmien.com/`
- App cho nhân viên (PWA):            `https://mau.tenmien.com/app`
- Admin Django:                       `https://mau.tenmien.com/admin/`

Giả sử subdomain bạn dùng là **mau.tenmien.com** (đổi cho đúng).

---

## 0) DNS
Tạo bản ghi **A** cho `mau` trỏ về IP VPS (trong trang quản lý tên miền).

## 1) Tải mã nguồn lên VPS
Nén thư mục `dali_pha_web` rồi upload (scp/rsync/WinSCP) vào VPS, ví dụ:
```
/var/www/dali_pha_web
```

## 2) Cài Python + môi trường ảo
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip nginx
cd /var/www/dali_pha_web
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3) Biến môi trường (bảo mật)
Tạo file `/var/www/dali_pha_web/.env` HOẶC đặt trực tiếp trong service (mục 5):
```
DJANGO_SECRET_KEY=<chuoi-ngau-nhien-dai>
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=mau.tenmien.com
DJANGO_CSRF_TRUSTED=https://mau.tenmien.com
```
(Sinh secret key: `python -c "import secrets;print(secrets.token_urlsafe(50))"`)

## 4) Khởi tạo CSDL + static
```bash
source venv/bin/activate
export DJANGO_ALLOWED_HOSTS=mau.tenmien.com
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser    # tài khoản vào /admin (tuỳ chọn)
```
Chạy thử: `python manage.py runserver 0.0.0.0:8001` rồi mở `http://IP_VPS:8001/app`.

## 5) Chạy bằng gunicorn + systemd (luôn online)
Tạo `/etc/systemd/system/phaweb.service`:
```ini
[Unit]
Description=Pha mau web
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/dali_pha_web
Environment=DJANGO_SECRET_KEY=<chuoi-ngau-nhien-dai>
Environment=DJANGO_DEBUG=0
Environment=DJANGO_ALLOWED_HOSTS=mau.tenmien.com
Environment=DJANGO_CSRF_TRUSTED=https://mau.tenmien.com
ExecStart=/var/www/dali_pha_web/venv/bin/gunicorn phaweb.wsgi:application --bind 127.0.0.1:8001 --workers 3 --timeout 180 --graceful-timeout 180
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo chown -R www-data:www-data /var/www/dali_pha_web   # để app GHI được recipes.json / base_colors.json
sudo systemctl daemon-reload
sudo systemctl enable --now phaweb
sudo systemctl status phaweb
```

## 6) Nginx cho subdomain
Tạo `/etc/nginx/sites-available/phaweb`:
```nginx
server {
    server_name mau.tenmien.com;

    location /static/ { alias /var/www/dali_pha_web/staticfiles/; }
    location /media/  { alias /var/www/dali_pha_web/media/; }

    # === BẢO MẬT: API KẾ TOÁN chỉ cho gọi từ CÙNG SERVER (localhost) ===
    # Chỉ khóa 3 endpoint kế toán (lương / chấm công / năng suất).
    # KHÔNG đụng /api/xu-ly-anh* -> web bán hàng tranhdali.vn vẫn gọi công khai bình thường.
    # Quản lý bấm "Test"/"Đẩy lại" dùng /ketoan-luong-test, /nang-suat-day-ketoan (không thuộc /api/) nên KHÔNG bị chặn.
    location ~ ^/api/(luong|ketoan|nang-suat)$ {
        allow 127.0.0.1;
        allow ::1;
        # Nếu ketoan & mau KHÔNG cùng máy, bỏ comment dòng dưới và điền IP server ketoan:
        # allow <IP_SERVER_KETOAN>;
        deny all;
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # AI xu ly dong bo toi ~150s (mac dinh 60s se 502)
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
    }
}
```

> **Cùng server → cho ketoan gọi qua `http://127.0.0.1/...`** (vd `http://127.0.0.1/api/nang-suat?...`),
> KHÔNG gọi qua `https://mau.tranhdali.vn/...` (đi vòng ra ngoài → nginx thấy IP công khai → bị `deny`).
> Nếu 2 app khác máy: thêm `allow <IP_SERVER_KETOAN>;` rồi `sudo nginx -t && sudo systemctl reload nginx`.
```bash
sudo ln -s /etc/nginx/sites-available/phaweb /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 7) HTTPS (BẮT BUỘC để cài PWA)
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d mau.tenmien.com
```
Certbot tự thêm HTTPS + chuyển hướng. PWA chỉ "Thêm vào màn hình chính" được khi có HTTPS.

## 8) Nhân viên cài app
Mở `https://mau.tenmien.com/app` trên điện thoại →
- Android/Chrome: bấm nút **"Cài app"** (hoặc menu ⋮ → "Thêm vào màn hình chính").
- iPhone/Safari: nút Chia sẻ → "Thêm vào MH chính".

---

## Cập nhật dữ liệu công thức
- Chủ vào `https://mau.tenmien.com/` để thêm/sửa công thức & màu gốc (lưu vào `pha/recipes.json`, `pha/base_colors.json`).
- HOẶC chép đè 2 file đó từ máy tính lên VPS rồi `sudo systemctl restart phaweb`.

## Lưu ý
- Dữ liệu công thức nằm ở `pha/recipes.json`, `pha/base_colors.json`; nhật ký pha ở `db.sqlite3`. Nhớ **sao lưu** các file này.
- Hiện app KHÔNG có đăng nhập cho `/app`. Nếu muốn giới hạn nhân viên, báo để thêm mã truy cập (Basic Auth ở nginx là nhanh nhất).
