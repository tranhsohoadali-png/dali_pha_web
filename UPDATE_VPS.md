# Cập nhật bản web trên VPS (DB công thức + Xuất Excel + Sao lưu)

Cập nhật này: chuyển công thức vào **database**, thêm nút **Xuất Excel báo cáo**,
và **sao lưu tự động** hằng ngày. KHÔNG mất dữ liệu công thức hiện có.

## A. Trên MÁY TÍNH (PowerShell) — đẩy code mới lên GitHub
```powershell
cd E:\dali_pha_web
git add .
git commit -m "DB recipes + excel report + backup"
git push
```

## B. Trên VPS (SSH / console web) — cập nhật
```bash
cd /var/www/dali_pha_web

# 1) SAO LƯU TRƯỚC (an toàn)
cp pha/recipes.json ~/recipes_backup.json
cp pha/base_colors.json ~/base_colors_backup.json
cp db.sqlite3 ~/db_backup.sqlite3 2>/dev/null || true

# 2) Lấy code mới
git pull origin main

# 3) Cài thư viện mới (openpyxl) + cập nhật DB
#    -> migrate sẽ tạo bảng Recipe và TỰ NHẬP công thức từ recipes.json vào DB
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py collectstatic --noinput
chown -R www-data:www-data .

# 4) Khởi động lại
systemctl restart phaweb

# 5) Kiểm tra số công thức đã vào DB
./venv/bin/python manage.py shell -c "from pha.models import Recipe; print('So cong thuc trong DB:', Recipe.objects.count())"

# 6) Bật SAO LƯU TỰ ĐỘNG hằng ngày (2h sáng)
chmod +x backup.sh
( crontab -l 2>/dev/null; echo "0 2 * * * /var/www/dali_pha_web/backup.sh >> /var/www/dali_pha_web/backups/cron.log 2>&1" ) | crontab -
```

### Nếu bước (2) `git pull` báo lỗi "local changes would be overwritten"
Nghĩa là có file dữ liệu bị sửa trên VPS. Làm:
```bash
git stash
git pull origin main
# khôi phục lại dữ liệu live từ bản backup vừa tạo:
cp ~/recipes_backup.json   pha/recipes.json
cp ~/base_colors_backup.json pha/base_colors.json
```
Rồi chạy tiếp từ bước (3).

## C. Kiểm tra sau cập nhật
- Mở `https://mau.tranhdali.vn/` → thấy **đủ công thức** như cũ (giờ đọc từ DB).
- Khu **Thống kê** có nút **"Xuất Excel"** → tải file `.xlsx` (2 sheet: Tổng theo màu + Chi tiết từng mẻ).
- Bước (5) in ra **đúng số công thức** bạn đang có.

## Ghi chú
- Từ giờ công thức lưu trong **db.sqlite3** (an toàn khi nhiều người sửa). File
  `recipes.json` chỉ còn là bản giống ban đầu, không dùng nữa.
- **Sao lưu**: cron chạy `backup.sh` mỗi ngày, nén `db.sqlite3` + các JSON vào
  thư mục `backups/` (giữ 14 bản gần nhất). Khôi phục: `tar -xzf backups/<file>.tar.gz` rồi `systemctl restart phaweb`.
- Nên thỉnh thoảng **tải 1 bản backup về máy** cho chắc (phòng VPS hỏng):
  `scp root@72.62.76.78:/var/www/dali_pha_web/backups/backup_*.tar.gz E:\`
