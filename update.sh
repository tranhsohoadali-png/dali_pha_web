#!/usr/bin/env bash
# Cập nhật phần mềm trên VPS sau khi đã `git pull`.
# Dùng:  cd /var/www/dali_pha_web && git pull && bash update.sh
set -e
cd "$(dirname "$0")"

echo ">> Sao lưu nhanh..."
cp pha/recipes.json ~/recipes_backup.json 2>/dev/null || true
cp pha/base_colors.json ~/base_colors_backup.json 2>/dev/null || true
cp db.sqlite3 ~/db_backup.sqlite3 2>/dev/null || true

echo ">> Cài thư viện hệ thống cho xử lý ảnh (opencv/shapely) + font cho ảnh chú giải..."
apt-get install -y libgl1 libglib2.0-0 libgeos-dev fonts-dejavu-core >/dev/null 2>&1 || true

echo ">> Cài/cập nhật thư viện Python (lần đầu có opencv sẽ tải ~100MB, hơi lâu)..."
./venv/bin/pip install -q -r requirements.txt

echo ">> Cập nhật DB + static..."
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
chown -R www-data:www-data .

echo ">> Khởi động lại dịch vụ..."
systemctl restart phaweb

echo ""
echo "============================================="
echo " XONG! Da cap nhat: https://mau.tranhdali.vn/"
echo "============================================="
