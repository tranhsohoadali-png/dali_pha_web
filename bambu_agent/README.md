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

## In thẻ tự cắt (tab "In thẻ" trên web)
Agent tự cắt file bằng **Bambu Studio** đã cài trên PC xưởng, rồi gửi thẳng máy in. Chuẩn bị 1 lần:

1. **Tạo template .3mf cho mỗi đầu phun** (bake sẵn cấu hình). Mở Bambu Studio → chọn máy A1 + đầu phun (0.2 hoặc 0.4) + nhựa + thông số (nhiệt mặc định…) → **để khay TRỐNG (xoá hết vật thể)** → `File → Save Project As` ra ví dụ `C:\dali\templates\tpl_A1_0.4.3mf`. Làm tương tự cho 0.2.
   - ⚠ **Máy A1 KHÔNG có đầu phun 0.3 chính hãng** (chỉ 0.2/0.4/0.6/0.8). Nếu bên anh ghi "0.3": hoặc thực ra là 0.4, hoặc anh lắp đầu 0.3 bên thứ 3 → khi đó tự tạo máy tùy chỉnh trong Bambu Studio (đặt `nozzle_diameter`=0.3) rồi lưu template `tpl_A1_0.3.3mf`.
2. Trong `config.json`, phần `"slicer"`:
   - `"bambu_exe"`: đường dẫn `bambu-studio.exe` (mặc định `C:\Program Files\Bambu Studio\bambu-studio.exe`).
   - `"templates"`: map đầu phun → file template, ví dụ `{"0.2": "C:\\dali\\templates\\tpl_A1_0.2.3mf", "0.4": "C:\\dali\\templates\\tpl_A1_0.4.3mf"}`.
3. Trên web tab **In thẻ**: tải **model STL** từng thẻ + khai môn, đầu phun, phút/thẻ, nhựa g/thẻ, tối đa/khay. Rồi tạo lệnh: chọn thẻ + số lượng (trộn nhiều thẻ được) hoặc đặt thời gian → agent tự cắt (đúng số lượng, đặt nhiệt nếu nhập) → gửi máy in. Kết quả (số thẻ, phút, nhựa) báo về web.

> Agent cắt bằng cách: `bambu-studio.exe <template.3mf> <stl…lặp theo số lượng> --arrange 1 --slice 1 [--nozzle-temperature …] --outputdir <tmp> --export-3mf out.gcode.3mf`, đọc `result.json` để lấy thời gian/nhựa, rồi nạp FTPS + lệnh in. Nếu 1 lệnh quá 1 khay sẽ báo "giảm số lượng".

## Bảo mật
- **Access Code** chỉ nằm trong `config.json` trên PC xưởng, không gửi lên web.
- `config.json` chứa mật khẩu máy in — đừng chia sẻ, đừng commit lên git.
