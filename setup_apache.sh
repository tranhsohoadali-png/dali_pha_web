#!/usr/bin/env bash
# Cài "Công thức pha + App điện thoại" lên VPS đang chạy APACHE2 (Ubuntu 24.04).
# KHÔNG đụng tới website tranhdali.vn hiện có. Chỉ thêm 1 VirtualHost cho subdomain.
# Cách dùng:  bash setup_apache.sh <subdomain> [email]
# Ví dụ:      bash setup_apache.sh mau.tranhdali.vn ban@gmail.com
set -e

DOMAIN="$1"
EMAIL="$2"
if [ -z "$DOMAIN" ]; then
  echo "Thiếu subdomain. Ví dụ:  bash setup_apache.sh mau.tranhdali.vn ban@gmail.com"
  exit 1
fi

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')"

echo ">>> [1/6] Cài gói cần thiết (KHÔNG cài/đụng nginx)..."
apt update -y
apt install -y python3-venv python3-pip
DEBIAN_FRONTEND=noninteractive apt install -y certbot python3-certbot-apache || true

echo ">>> [2/6] Tạo venv + cài thư viện Python (Django, numpy, gunicorn)..."
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

echo ">>> [3/6] Khởi tạo CSDL + gom static..."
export DJANGO_SECRET_KEY="$SECRET"
export DJANGO_DEBUG=0
export DJANGO_ALLOWED_HOSTS="$DOMAIN"
export DJANGO_CSRF_TRUSTED="https://$DOMAIN"
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
chown -R www-data:www-data "$APP_DIR"

echo ">>> [4/6] Tạo dịch vụ gunicorn (chạy nội bộ cổng 8001)..."
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

echo ">>> [5/6] Tạo VirtualHost Apache cho $DOMAIN -> 127.0.0.1:8001 ..."
a2enmod proxy proxy_http headers ssl rewrite >/dev/null 2>&1 || true
cat >/etc/apache2/sites-available/$DOMAIN.conf <<EOF
<VirtualHost *:80>
    ServerName $DOMAIN
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"
    ProxyPass / http://127.0.0.1:8001/
    ProxyPassReverse / http://127.0.0.1:8001/
    ErrorLog \${APACHE_LOG_DIR}/$DOMAIN-error.log
    CustomLog \${APACHE_LOG_DIR}/$DOMAIN-access.log combined
</VirtualHost>
EOF
a2ensite "$DOMAIN.conf" >/dev/null
apache2ctl configtest && systemctl reload apache2

echo ">>> [6/6] Bật HTTPS (Let's Encrypt qua Apache)..."
if [ -n "$EMAIL" ]; then
  certbot --apache -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect \
    || echo "!! Certbot chưa cấp được chứng chỉ. Kiểm tra DNS $DOMAIN đã trỏ đúng IP chưa (dnschecker.org), rồi chạy: certbot --apache -d $DOMAIN"
else
  echo "(Chưa nhập email -> bỏ qua HTTPS). Chạy sau:  certbot --apache -d $DOMAIN"
fi

echo ""
echo "================================================================"
echo " HOAN TAT!"
echo "   Quan ly cong thuc:  https://$DOMAIN/"
echo "   App nhan vien:       https://$DOMAIN/app"
echo "   (Tao tai khoan admin:  cd $APP_DIR && ./venv/bin/python manage.py createsuperuser )"
echo " Website tranhdali.vn KHONG bi anh huong."
echo "================================================================"
