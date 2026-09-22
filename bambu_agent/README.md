# DALI Bambu Agent (nối máy A1 realtime)

Chương trình nhỏ chạy trên **1 PC ở xưởng** (cùng WiFi/LAN với máy in Bambu A1). Nó đọc
trạng thái máy qua MQTT rồi báo về web `mau.tranhdali.vn`, và nhận lệnh in / tạm dừng /
tiếp tục / dừng từ web để điều khiển máy.

> VPS ở xa không tự với tới máy in trong LAN xưởng — nên **bắt buộc** có agent này chạy ở
> xưởng thì tab **Realtime** mới thấy máy và bấm in được.

## 1. Chuẩn bị trên MÀN HÌNH từng máy A1
1. **Cài đặt (bánh răng) → General → bật "LAN Only Mode"**.
2. Ghi lại **Access Code** (mục WLAN/Network) — đây là mật khẩu để agent nối máy.
3. Ghi lại **Serial** (mục Device) và **IP** của máy trong mạng.
4. Cắm sẵn **thẻ microSD** vào máy (để nạp file in vào thẻ).

> Máy A1 chỉ cho ~2–3 kết nối cùng lúc. Khi agent chạy mà bị rớt, hãy tắt Bambu Studio /
> Bambu Handy đang mở.

## 2. Trên web (tab Realtime của /quan-ly-in)
- Vào tab **Máy in**, thêm/sửa từng máy và điền **Serial** (khớp serial máy) để web ghép
  đúng trạng thái. *Không cần nhập Access Code lên web.*
- Vào tab **Realtime**, bấm **Chép** để lấy **Khoá agent**.

## 3. Trên PC xưởng
1. Cài **Python 3** (python.org — nhớ tích *Add Python to PATH*).
2. Chép cả thư mục `bambu_agent` này về PC.
3. Chép `config.example.json` → **`config.json`**, rồi điền:
   - `server`: `https://mau.tranhdali.vn`
   - `key`: dán **Khoá agent** vừa chép ở web.
   - `printers`: mỗi máy 1 khối `{name, ip, serial, access_code}`.
4. Chạy: nhấp đúp **`run.bat`** (Windows) — hoặc mở terminal:
   ```
   pip install -r requirements.txt
   python bambu_agent.py
   ```
5. Để cửa sổ đó chạy nền. Quay lại web tab **Realtime** — sau vài giây máy sẽ hiện trạng
   thái realtime (tiến độ %, nhiệt, thời gian còn lại) và có nút In / Tạm dừng / Dừng.

## Cho chạy tự động khi mở máy (tùy chọn)
- Windows: nhấn `Win+R` → `shell:startup` → tạo shortcut trỏ tới `run.bat`.

## Xử lý sự cố
- **Máy báo offline / connect rc=…**: sai Access Code, sai IP, hoặc chưa bật LAN Mode.
- **"MQTT command verification failed"** khi in: bật **LAN Only Mode** (và Developer Mode nếu
  firmware mới có) trên máy.
- **Nạp file chậm**: FTPS của Bambu ~150 KB/s là bình thường; chờ vài chục giây.
- **Agent offline trên web**: kiểm tra PC xưởng có mạng ra Internet không, `key` đã đúng chưa
  (nếu bấm "Đổi khoá" trên web thì phải cập nhật lại `config.json`).

## Bảo mật
- **Access Code** chỉ nằm trong `config.json` trên PC xưởng, không gửi lên web.
- `config.json` chứa mật khẩu máy in — đừng chia sẻ, đừng commit lên git.
