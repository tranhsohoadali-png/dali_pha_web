# SKILL NGHIỆP VỤ: Xử lý ảnh khách thành tranh tô màu số (có Google AI)

> Tài liệu mô tả CHÍNH XÁC theo mã nguồn ngày 12/06/2026.
> Hệ thống liên quan: `tranhdali.vn` (web bán hàng, Laravel) ↔ `mau.tranhdali.vn` (phần mềm màu, Django) ↔ **Google Gemini API**.

---

## 1. Trả lời nhanh: AI nào đang được dùng?

| Việc | Model Google | Vai trò |
|---|---|---|
| **Vẽ lại / tăng cường ảnh khách** | `gemini-2.5-flash-image` (tên nội bộ "Nano Banana") | Nhận ảnh + câu lệnh → trả về MỘT ảnh mới đã vẽ lại theo phong cách yêu cầu |
| Đếm số màu từ bảng chú giải (tính năng phụ cho nhân viên) | `gemini-2.5-flash` | Đọc ảnh tranh số hoá có cột chú giải 1,2,3… → trả về con số |

**Khâu đánh số + khớp mã sơn DALI KHÔNG dùng AI** — đó là thuật toán OpenCV chạy trên máy chủ của shop (miễn phí, không phụ thuộc Google).

---

## 2. Sơ đồ luồng đầy đủ — đầu vào / đầu ra từng bước

```
KHÁCH (điện thoại)                LARAVEL tranhdali.vn            DJANGO mau.tranhdali.vn                GOOGLE
      │                                  │                                │                                │
 1. chọn ảnh ──► 2. nén trên máy ──► 3. kiểm lượt ──► 4. POST /api/xu-ly-anh ──► 5. lưu file + tạo job ──► trả id (~1s)
      │                                  │                                │
      │◄─── 6. hỏi trạng thái mỗi 3 giây (poll) ◄─────────────────────────┤
      │                                  │                                ├── 7. AI vẽ lại ảnh ──────────► Gemini
      │                                  │                                ├── 8. đánh số + khớp mã DALI (OpenCV)
      │◄─── 9. JSON kết quả (3 URL ảnh + bảng màu) ◄──────────────────────┤
 10. hiện ảnh gốc + ảnh AI, chọn cỡ/màu, đặt hàng
```

### Bước 1-2 — Trình duyệt khách (file `thiet-ke.blade.php`)
- **Vào:** ảnh PNG/JPG/WEBP bất kỳ (ảnh điện thoại 4–10MB vẫn nhận).
- **Xử lý:** nén ngay trên máy khách bằng canvas → JPEG chất lượng 0.85, cạnh dài ≤ **1800px** (~0.3–0.6MB) — tránh giới hạn upload 1MB của nginx và gửi nhanh trên 4G.
- **Ra:** file `ten-anh.jpg` + `device_id` (mã máy lưu localStorage).

### Bước 3 — Kiểm lượt (Laravel `ThietKeController::generate`)
- Mỗi máy (device_id) có **3 lượt miễn phí**, **+5 lượt** sau mỗi đơn đặt thành công. IP trong danh sách máy test (`thietke_test_ips`) = vô hạn.
- Hết lượt → trả lỗi `no_quota` (khách thấy lời mời đặt hàng / gửi Zalo). Còn lượt → trừ 1 lượt NGAY khi job khởi động, ghi nhớ job↔máy trong cache 2 giờ để **hoàn lượt nếu job lỗi**.

### Bước 4 — Gọi sang phần mềm màu
- **POST** `https://mau.tranhdali.vn/api/xu-ly-anh`, header `X-API-Key: <THIETKE_API_KEY>`.
- Tham số gửi đi:

| Tham số | Giá trị trang /thiet-ke đang gửi | Ý nghĩa |
|---|---|---|
| `image` | file ảnh đã nén | ảnh khách |
| `enhance` | `1` | bật tăng cường AI |
| `preset` | `anime` (mặc định — khách không được chọn) | gói phong cách + thông số tách màu |
| `print_size` | `40x50` | cỡ in (cm) → quyết định cỡ số in trên tranh |
| `color_limit` | `0` | 0 = theo preset, tối đa 60 |

### Bước 5 — Django nhận job (`api_xu_ly_anh`)
- Kiểm khoá: `THIETKE_API_KEY` trong AppSetting **phải khớp** với khoá bên admin tranhdali.vn → sai = lỗi **401**.
- Lưu file vào `/media/` tên `YYYY-MM-DD_HH-MM-SS_api_<tên>.jpg`; tạo bản ghi `ImageResult` (status=processing, user='api').
- Đẩy vào hàng đợi **ThreadPoolExecutor 2 luồng** → tối đa **2 job chạy cùng lúc**, job thứ 3 trở đi xếp hàng.
- **Trả về NGAY** (~1 giây): `{ok:true, status:'processing', id:123}` — nhờ vậy không bao giờ dính timeout 60s của nginx.

### Bước 6 — Khách chờ (poll)
- Laravel hỏi `…/api/xu-ly-anh-trang-thai?id=123` **mỗi 3 giây**, tối đa 10 phút (phía trình duyệt).
- Khách có thể **ẩn cửa sổ chờ / thoát hẳn trang** — job vẫn chạy trên server; quay lại trong 15 phút trang tự nối tiếp; xong có **chuông + thông báo**.
- Job kẹt processing quá **15 phút** → tự đánh dấu LỖI (thường do server hết RAM/khởi động lại) → khách được hoàn lượt.

### Bước 7 — Tăng cường AI (`ai_enhance.enhance_image`) ★ phần dùng Google
- **Vào:** ảnh khách (thu nhỏ còn ≤ **1536px** cạnh dài để Google xử lý nhanh, ít lỗi 504) + **câu lệnh (prompt) theo preset** + (tuỳ chọn, mặc định TẮT) tối đa 3 ảnh mẫu phong cách.
- **Gọi:** `gemini-2.5-flash-image`, timeout **150 giây/lần**; lỗi tạm của Google (504/503/quá tải) → nghỉ 3 giây **tự thử lại 1 lần**.
- **Trần cứng cả khâu AI: 210 giây** — quá trần thì **BỎ QUA AI, xử lý ảnh gốc luôn** (khách vẫn nhận kết quả, kèm cảnh báo nhẹ trong trường `warn`). Google sập không bao giờ làm tắc hàng đợi.
- **Ra:** file `<tên>_ai.png` (đã chuẩn hoá PNG/RGB).
- Nếu thiếu khoá `GOOGLE_API_KEY`, bị chặn nội dung, hết hạn mức → cũng rơi vào nhánh "bỏ AI, dùng ảnh gốc".

### Bước 8 — Đánh số + khớp mã sơn (KHÔNG AI, OpenCV `index_color`)
- **Vào:** ảnh (đã hoặc chưa tăng cường) + thông số preset: `color_limit` (số màu), `min_area` (xoá mảng nhỏ hơn N pixel), `smooth` (làm mượt biên), `print_long_cm` (cỡ in → cỡ chữ số).
- **Việc làm:** tách ảnh thành các mảng màu phẳng → vẽ viền + in số vào từng mảng → tính % diện tích từng màu → **khớp từng màu với mã sơn DALI gần nhất** (`nearest_dali`, ví dụ BCA.10, BLA.4).
- **Ra:** `*_result.png` (bản đồ đánh số có chú giải), `*_design.png` (bản thiết kế màu phẳng), bảng `colors = [số thứ tự, mã hex, mã sơn DALI, % diện tích]`.

### Bước 9 — JSON kết quả trả cho Laravel

```json
{
  "ok": true, "status": "done", "id": 123,
  "original":  "https://mau.tranhdali.vn/media/2026-06-12_..._api_anh.jpg",
  "enhanced":  "https://mau.tranhdali.vn/media/2026-06-12_..._api_anh_ai.png",
  "img_output":"https://mau.tranhdali.vn/media/2026-06-12_..._result.png",
  "colors": [[...10 dòng/trang...]],
  "warn": ""   // có chữ = AI đã bị bỏ qua, kết quả làm từ ảnh gốc
}
```

### Bước 10 — Hiển thị & đời sống dữ liệu
- Khách **chỉ thấy**: ảnh gốc + ảnh tăng cường AI (bấm phóng to được). **Bản đồ màu đánh số (`img_output`) KHÔNG cho khách xem** — chỉ đính vào ghi chú đơn hàng cho shop làm tranh.
- Kết quả lưu localStorage của máy khách **24 giờ** (tải lại trang vẫn còn); file trên server cũng được **dọn sau ~24 giờ** (`_prune_image_results`) — đó là lý do trang ghi "bản thiết kế chỉ lưu 24 giờ".
- Khi khách đặt đơn: tạo đơn mã `TK-XXXXXX`, ghi chú gồm gói (cỡ + số màu + giá) + link `img_output` + link `enhanced`; máy khách +5 lượt.

---

## 3. Ba preset phong cách (đóng gói prompt + thông số tách màu)

| Preset | Số màu | Phong cách AI vẽ lại | Khi nào dùng |
|---|---|---|---|
| `anime` (mặc định của /thiet-ke) | 12 | Sticker/chibi nét đậm kín, nền giản lược mạnh, mảng to dễ tô | Tranh ít màu, trẻ em, thú cưng hoạt hình |
| `photo` | 40 (30–48) | **Giữ ĐÚNG người thật** — posterize chân dung như Image Trace, không biến thành anime, ưu tiên chi tiết mặt | Ảnh chân dung/gia đình muốn giống thật |
| `design` | 16 | Ảnh vector/phẳng sẵn → chỉ chuẩn hoá nét + màu | Logo, tranh thiết kế sẵn |

> ⚠️ **Điểm nghiệp vụ đáng lưu ý:** trang /thiet-ke hiện **luôn gửi `preset=anime`** (khách không được chọn). Ảnh gia đình/chân dung sẽ bị vẽ lại kiểu hoạt hình 12 màu. Nếu muốn tranh chân dung giống thật (30–48 màu, giá bán cao hơn) → cần (a) đổi mặc định sang `photo`, hoặc (b) cho khách tự chọn phong cách trước khi tạo. Sửa được trong 1 buổi — báo Claude khi cần.

---

## 4. Chi phí & hạn mức (để tính giá bán)

- Mỗi lượt tạo có bật AI = **1 lần gọi `gemini-2.5-flash-image`** (2 lần nếu lần đầu lỗi tạm). Giá tham khảo của Google ≈ **0,039 USD/ảnh ≈ ~1.000đ/lượt** (kiểm tra giá mới nhất tại ai.google.dev/pricing — có thể thay đổi).
- 3 lượt miễn phí/máy ≈ ~3.000đ chi phí AI tối đa cho 1 khách chưa mua — đã được bù bằng cơ chế cọc/đặt hàng +5 lượt.
- Khoá Google hết tiền/hạn mức → hệ thống **không sập**: khách vẫn nhận bản đồ màu làm từ ảnh gốc (chất lượng thấp hơn), trường `warn` có ghi chú.
- Đếm màu (`gemini-2.5-flash`) rẻ hơn nhiều, chỉ nhân viên dùng, không đáng kể.

## 5. Khoá & cấu hình — ai giữ gì, đổi ở đâu

| Khoá / cấu hình | Nơi nhập | Ghi chú |
|---|---|---|
| `GOOGLE_API_KEY` (khoá Gemini, MẤT TIỀN) | mau.tranhdali.vn/cai-dat-ai | Lưu trong DB; dự phòng đọc biến môi trường `GOOGLE_API_KEY`/`GEMINI_API_KEY` |
| `THIETKE_API_KEY` (khoá nội bộ 2 hệ thống) | **PHẢI KHỚP 2 NƠI**: mau /cai-dat-ai ↔ tranhdali.vn admin → Cài đặt → API màu | Bấm "Tạo khoá ngẫu nhiên" một bên thì PHẢI dán lại bên kia, nếu không khách gặp lỗi 401 |
| Lượt miễn phí / thưởng | code Laravel `DesignQuota` FREE=3, ORDER_BONUS=5 | đổi cần sửa code |
| Máy test vô hạn | tranhdali.vn admin → Cài đặt → `thietke_test_ips` | nhập IP, có nút "➕ Thêm IP này" |
| Bảng giá bán | tranhdali.vn/admin/thiet-ke-gia | bấm vào ô là sửa, tự lưu |
| Model/timeout/prompt AI | biến môi trường trên server mau: `AI_ENHANCE_MODEL`, `AI_TIMEOUT_MS` (150000), `AI_MAX_EDGE` (1536), `AI_ENHANCE_PROMPT`, `AI_PROMPT_PHOTO`, `AI_PROMPT_DESIGN` | trần cứng 210s nằm trong code (`AI_BUDGET_S`) |

## 6. Lỗi thường gặp & cách xử lý nhanh

| Hiện tượng | Nguyên nhân | Xử lý |
|---|---|---|
| Khách thấy "Khoá API… KHÔNG khớp" | THIETKE_API_KEY 2 bên lệch nhau | Dán lại cùng một khoá ở cả 2 trang cài đặt |
| Kết quả không đẹp, `warn` = "Bỏ qua tăng cường AI (504…)" | Google quá tải/ảnh phức tạp quá 210s | Bình thường, thử lại lúc khác; lặp nhiều thì giảm `AI_MAX_EDGE` |
| "Google AI không trả về ảnh" | Nội dung bị chặn (ảnh nhạy cảm) hoặc hết hạn mức khoá | Kiểm tra hạn mức tại console Google; đổi ảnh |
| Job lỗi "Quá 15 phút chưa xong" | Server hết RAM / restart giữa chừng | `journalctl -u phaweb -n 50`; `dmesg \| grep -i oom`; khách đã được tự hoàn lượt |
| Khách kêu mất lượt oan | Job lỗi nhưng poll chưa chạy đến lúc hoàn | Lượt hoàn tự động ở lần poll kế; kiểm tra bảng DesignQuota nếu cần cộng tay |

## 7. Lệnh theo dõi nhanh (chạy trong terminal VPS)

```bash
# 5 job gần nhất + lỗi nếu có
cd /var/www/dali_pha_web && ./venv/bin/python manage.py shell -c "
from pha.models import ImageResult
for r in ImageResult.objects.order_by('-created_time')[:5]:
    print(r.id,'|',r.status,'|',r.created_time.strftime('%d/%m %H:%M'),'|',(r.error_message or '')[:120],'|',r.name[:45])"

# nhật ký dịch vụ xử lý ảnh
journalctl -u phaweb -n 25 --no-pager
```
