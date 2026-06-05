#!/usr/bin/env bash
# Sao lưu dữ liệu phần mềm pha màu: database + các file JSON.
# Giữ lại 14 bản gần nhất. Đặt chạy hằng ngày bằng cron (xem hướng dẫn dưới).
set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BK="$APP_DIR/backups"
mkdir -p "$BK"
TS="$(date +%Y%m%d_%H%M%S)"

tar -czf "$BK/backup_$TS.tar.gz" -C "$APP_DIR" \
    db.sqlite3 \
    pha/base_colors.json \
    pha/recipes.json 2>/dev/null || true

# Chỉ giữ 14 bản mới nhất
ls -1t "$BK"/backup_*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f

echo "Đã sao lưu: $BK/backup_$TS.tar.gz"

# ----------------------------------------------------------------------
# CÀI CHẠY TỰ ĐỘNG HẰNG NGÀY (2h sáng) — chạy 1 lần trên VPS:
#   chmod +x /var/www/dali_pha_web/backup.sh
#   ( crontab -l 2>/dev/null; echo "0 2 * * * /var/www/dali_pha_web/backup.sh >> /var/www/dali_pha_web/backups/cron.log 2>&1" ) | crontab -
#
# KHÔI PHỤC khi cần:
#   cd /var/www/dali_pha_web
#   tar -xzf backups/backup_YYYYmmdd_HHMMSS.tar.gz
#   systemctl restart phaweb
# ----------------------------------------------------------------------
