#!/usr/bin/env bash
# Cài đặt TỰ ĐỘNG phần mềm "Công thức pha + App điện thoại" lên VPS (Ubuntu/Debian).
# Cách dùng:   bash setup.sh <subdomain> [email]
# Ví dụ:       bash setup.sh mau.tenmien.com ban@gmail.com
set -e

DOMAIN="$1"
EMAIL="$2"
if [ -z "$DOMAIN" ]; then
  echo "Thiếu subdomain. Ví dụ:  bash setup.sh mau.tenmien.com ban@gmail.com"
  exit 1
fi

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))' 2>/dev/null || openssl rand -base64 48)"

echo ">>> [1/7] Cài gói hệ thống..."
apt update -y
apt install -y python3 python3-venv python3-pip nginx
DEBIAN_FRONTEND=noninteractive apt install -y certbot python3-certbot-nginx || true

echo ">>> [2/7] Tạo môi trường ảo + cài thư viện Python..."
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo ">>> [3/7] Khởi tạo CSDL + gom static..."
export DJANGO_SECRET_KEY="$SECRET"
export DJANGO_DEBUG=0
export DJANGO_ALLOWED_HOSTS="$DOMAIN"
export DJANGO_CSRF_TRUSTED="https://$DOMAIN"
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput

echo ">>> [4/7] Phân quyền dữ liệu..."
chown -R www-data:www-data "$APP_DIR"

echo ">>> [5/7] Tạo dịch vụ chạy nền (systemd)..."
cat >/etc/systemd/system/phaweb.service <<EOF
[Unit]
Description=Pha mau web
After=network.target

[Service]
User=www-data
WorkingDirectory=$APP_DIR
Environment=DJANGO_SECRET_KEY=$SECRET
Environment=DJANGO_DEBUG=0
Environment=DJANGO_ALLOWED_HOSTS=$DOMAIN
Environment=DJANGO_CSRF_TRUSTED=https://$DOMAIN
ExecStart=$APP_DIR/venv/bin/gunicorn phaweb.wsgi:application --bind 127.0.0.1:8001 --workers 3
Restart=always

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now phaweb

echo ">>> [6/7] Cấu hình nginx cho $DOMAIN..."
cat >/etc/nginx/sites-available/phaweb <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 20M;
    location /static/ { alias $APP_DIR/staticfiles/; }
    location /media/  { alias $APP_DIR/media/; }
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/phaweb /etc/nginx/sites-enabled/phaweb
# (KHÔNG xoá site mặc định / site khác — tránh ảnh hưởng web đang chạy)
nginx -t && systemctl reload nginx

echo ">>> [7/7] Bật HTTPS (Let's Encrypt)..."
if [ -n "$EMAIL" ]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect \
    || echo "!! Certbot chưa cấp được chứng chỉ. Kiểm tra DNS đã trỏ đúng IP chưa, rồi chạy: certbot --nginx -d $DOMAIN"
else
  echo "(Bỏ qua HTTPS vì chưa nhập email). Chạy sau:  certbot --nginx -d $DOMAIN"
fi

echo ""
echo "================================================================"
echo " HOAN TAT!"
echo "   Quan ly cong thuc:  https://$DOMAIN/"
echo "   App nhan vien:       https://$DOMAIN/app"
echo "   Admin:               https://$DOMAIN/admin/   (tao tai khoan:"
echo "       cd $APP_DIR && ./venv/bin/python manage.py createsuperuser )"
echo "================================================================"
