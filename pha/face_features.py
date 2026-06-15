"""Phát hiện & BẢO VỆ ngũ quan (mắt, lông mày, mũi, miệng) — CHỈ dùng cho ảnh chân
dung thật (preset 'photo', cờ face_priority=True). Trả mask 0/255 cùng kích thước
ảnh, đánh dấu vùng cần GIỮ NÉT và KHÔNG làm mượt mạnh.

Hai hướng (hướng 2 trong tư vấn cho khách):
  - LANDMARK CHÍNH XÁC: nếu cài 'mediapipe' -> FaceMesh khoanh đúng polygon mắt/
    lông mày/mũi/môi (kể cả mặt nghiêng). KHÔNG bắt buộc cài.
  - KHÔNG CẦN CÀI THÊM: Haar cascade mắt + mắt-đeo-KÍNH + dò mặt, cộng SUY LUẬN
    HÌNH HỌC cho mũi/miệng theo tỉ lệ khuôn mặt. Đi kèm sẵn OpenCV.

Triết lý: khâu này CHỈ để tô đậm chỗ cần giữ; mọi lỗi đều nuốt êm -> trả None
(pipeline tự chạy như cũ bằng heuristic hình học sẵn có). KHÔNG được làm hỏng job.
"""
import os

import cv2
import numpy as np

# Tắt khẩn cấp nếu cần (đặt biến môi trường FACE_FEATURES_OFF=1).
_OFF = bool(os.environ.get('FACE_FEATURES_OFF'))

_CASC = {}


def _casc(name):
    """Nạp + cache CascadeClassifier (đi kèm OpenCV)."""
    c = _CASC.get(name)
    if c is None:
        c = cv2.CascadeClassifier(cv2.data.haarcascades + name)
        _CASC[name] = c
    return c


def _mediapipe_mask(rgb):
    """Mask polygon ngũ quan bằng mediapipe FaceMesh (nếu cài). None nếu không có/không thấy mặt."""
    try:
        import mediapipe as mp
    except Exception:                       # chưa cài -> bỏ qua êm
        return None
    try:
        H, W = rgb.shape[:2]
        conns = mp.solutions.face_mesh_connections
        # Gom các nhóm điểm cho từng bộ phận (mỗi nhóm là tập cặp cạnh).
        groups = []
        for attr in ('FACEMESH_LIPS', 'FACEMESH_LEFT_EYE', 'FACEMESH_RIGHT_EYE',
                     'FACEMESH_LEFT_EYEBROW', 'FACEMESH_RIGHT_EYEBROW',
                     'FACEMESH_NOSE', 'FACEMESH_LEFT_IRIS', 'FACEMESH_RIGHT_IRIS'):
            g = getattr(conns, attr, None)
            if g:
                groups.append(g)
        if not groups:
            return None
        # Dò trên bản THU NHỎ (toạ độ landmark đã chuẩn hoá 0..1 -> dựng mask ở
        # full H,W vẫn đúng) -> đỡ phình CPU/RAM trên ảnh 2x lớn.
        small = rgb
        if max(H, W) > 900:
            sc = 900.0 / max(H, W)
            small = cv2.resize(rgb, (int(W * sc), int(H * sc)), interpolation=cv2.INTER_AREA)
        fm = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True, max_num_faces=4, refine_landmarks=True,
            min_detection_confidence=0.4)
        try:
            res = fm.process(small)
        finally:
            fm.close()
        if not getattr(res, 'multi_face_landmarks', None):
            return None
        mask = np.zeros((H, W), np.uint8)
        for fl in res.multi_face_landmarks:
            pts = [(int(p.x * W), int(p.y * H)) for p in fl.landmark]
            n = len(pts)
            for g in groups:
                idxs = sorted({i for pair in g for i in pair if i < n})
                if len(idxs) < 3:
                    continue
                poly = np.array([pts[i] for i in idxs], np.int32)
                hull = cv2.convexHull(poly)
                cv2.fillConvexPoly(mask, hull, 255)
        return mask if mask.any() else None
    except Exception:
        return None


def _detect_faces(gray):
    """Dò khuôn mặt (chính diện): thử cascade default rồi alt. Trả list (x,y,w,h)."""
    for name in ('haarcascade_frontalface_default.xml', 'haarcascade_frontalface_alt.xml'):
        try:
            f = _casc(name).detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
        except Exception:
            f = []
        if len(f):
            return f
    return []


def _haar_mask(rgb):
    """Mask ngũ quan bằng Haar (mắt + mắt-đeo-kính) + suy luận hình học mũi/miệng.
    Không thấy mặt -> None."""
    H, W = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # Thu nhỏ để dò nhanh; toạ độ trả về nhân ngược (inv) về ảnh gốc.
    scale = 1.0
    if max(H, W) > 900:
        scale = 900.0 / max(H, W)
        gray = cv2.resize(gray, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
    geq = cv2.equalizeHist(gray)
    faces = _detect_faces(geq)
    if len(faces) == 0:
        return None
    inv = 1.0 / scale
    mask = np.zeros((H, W), np.uint8)

    def fill(x0, y0, x1, y1):
        x0 = max(0, int(x0 * inv)); y0 = max(0, int(y0 * inv))
        x1 = min(W, int(x1 * inv)); y1 = min(H, int(y1 * inv))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = 255

    for (fx, fy, fw, fh) in faces:
        # MẮT (kèm LÔNG MÀY): dò trong 60% trên của mặt. Thử cascade thường rồi
        # cascade-đeo-kính (ảnh khách hay đeo kính). Lấy tối đa 2 đốm to nhất.
        ey_h = int(fh * 0.6)
        eroi = geq[fy:fy + ey_h, fx:fx + fw]
        eyes = []
        if eroi.size:
            ms = max(12, int(fw * 0.12))
            for ecasc in ('haarcascade_eye.xml', 'haarcascade_eye_tree_eyeglasses.xml'):
                try:
                    eyes = _casc(ecasc).detectMultiScale(eroi, 1.1, 4, minSize=(ms, ms))
                except Exception:
                    eyes = []
                if len(eyes):
                    break
        eyes = sorted(eyes, key=lambda e: -e[2] * e[3])[:2]
        for (ex, ey, ew, eh) in eyes:
            gx, gy = fx + ex, fy + ey
            # nới lên 0.9*eh để ôm cả LÔNG MÀY; nới ngang nhẹ.
            fill(gx - ew * 0.12, gy - eh * 0.9, gx + ew * 1.12, gy + eh * 1.12)
        if len(eyes) == 0:
            # Không bắt được mắt -> bảo vệ dải mắt+mày theo tỉ lệ mặt (2 bên).
            fill(fx + fw * 0.12, fy + fh * 0.22, fx + fw * 0.46, fy + fh * 0.44)
            fill(fx + fw * 0.54, fy + fh * 0.22, fx + fw * 0.88, fy + fh * 0.44)
        # MŨI: dải giữa (sống + cánh mũi) theo tỉ lệ — ổn định hơn cascade mũi.
        fill(fx + fw * 0.36, fy + fh * 0.42, fx + fw * 0.64, fy + fh * 0.66)
        # MIỆNG: dải dưới — luôn bảo vệ (đường viền môi, khoé miệng).
        fill(fx + fw * 0.24, fy + fh * 0.62, fx + fw * 0.78, fy + fh * 0.87)
    return mask if mask.any() else None


# ---- YuNet (DNN) — dò mặt CHẮC hơn Haar rất nhiều (mặt nhỏ/nghiêng/che một phần)
# + trả 5 ĐIỂM MỐC (2 mắt, mũi, 2 khoé miệng) -> khoanh ngũ quan CHÍNH XÁC.
# NẠP 2 MODEL kèm repo: 2023mar (cho cv2 >= ~4.7) và 2022mar (cho cv2 4.5.x). MỖI bản
# cv2 thường chỉ chạy được 1 trong 2 (bản kia ném lỗi DNN) -> THỬ LẦN LƯỢT rồi NHỚ bản
# chạy được. cv2 quá cũ (không có FaceDetectorYN) / không bản nào chạy -> lùi Haar.
_MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
_YUNET_FILES = ('yunet_2023mar.onnx', 'yunet_2022mar.onnx')   # thử bản MỚI trước
_YUNET_HAS = hasattr(cv2, 'FaceDetectorYN')
_yunet_pick = {'done': False, 'path': None}     # cache bản chạy được (None = không có)


def _m32(v):
    """Làm tròn về bội số 32 GẦN NHẤT (YuNet bắt buộc cạnh ảnh chia hết 32)."""
    return max(32, int(round(v / 32.0)) * 32)


def _yunet_run(model_path, rgb, conf, long_side):
    """Chạy 1 model YuNet trên ảnh RGB -> list dict {box,lms,score} full-res. Có thể
    NÉM lỗi nếu model không hợp cv2 hiện tại (caller bắt để thử bản khác)."""
    H, W = rgb.shape[:2]
    sc = float(long_side) / max(H, W) if max(H, W) > long_side else 1.0
    dw, dh = _m32(W * sc), _m32(H * sc)
    bgr = cv2.cvtColor(cv2.resize(rgb, (dw, dh), interpolation=cv2.INTER_AREA),
                       cv2.COLOR_RGB2BGR)
    det = cv2.FaceDetectorYN.create(model_path, '', (dw, dh), conf, 0.3, 5000)
    _rv, faces = det.detect(bgr)
    if faces is None or len(faces) == 0:
        return []
    sx, sy = W / float(dw), H / float(dh)
    out = []
    for f in faces:
        bx0, by0 = max(0, int(f[0] * sx)), max(0, int(f[1] * sy))
        bx1, by1 = min(W, int((f[0] + f[2]) * sx)), min(H, int((f[1] + f[3]) * sy))
        if bx1 <= bx0 or by1 <= by0:               # box ngoài khung -> bỏ
            continue
        out.append({
            'box': (bx0, by0, bx1 - bx0, by1 - by0),   # KẸP trong khung (YuNet có thể trả lố)
            'lms': (f[4:14].reshape(5, 2) * np.array([sx, sy], np.float32)),
            'score': float(f[14])})
    out.sort(key=lambda d: -d['box'][2] * d['box'][3])
    return out[:4]


def _yunet_faces(rgb, conf=0.7, long_side=800):
    """Dò mặt bằng YuNet. Trả list dict {box:(x,y,w,h), lms:(5,2) float, score} full-res;
    [] nếu không có model chạy được / không thấy / lỗi. Tạo detector MỚI mỗi lần (DNN cv2
    KHÔNG an toàn đa luồng — VPS chạy 2 job song song)."""
    if _OFF or not _YUNET_HAS or rgb is None or rgb.ndim != 3:
        return []
    try:
        if _yunet_pick['done']:                       # đã biết bản chạy được
            mp = _yunet_pick['path']
            return _yunet_run(mp, rgb, conf, long_side) if mp else []
        for name in _YUNET_FILES:                      # lần đầu: thử từng bản, nhớ lại
            mp = os.path.join(_MODEL_DIR, name)
            if not os.path.exists(mp):
                continue
            try:
                res = _yunet_run(mp, rgb, conf, long_side)
                _yunet_pick['path'], _yunet_pick['done'] = mp, True
                return res
            except Exception:
                continue
        _yunet_pick['path'], _yunet_pick['done'] = None, True   # không bản nào hợp
        return []
    except Exception:
        return []


def _landmark_mask(rgb, faces):
    """Mask 0/255 NGŨ QUAN dựng từ 5 điểm mốc YuNet — chính xác hơn suy luận hình học.
    Khoanh: 2 mắt, 2 lông mày, mũi (sống+cánh), miệng, và 2 TAI. Xoay theo độ nghiêng
    đầu (góc đường nối 2 mắt) -> đúng cả mặt nghiêng. Bỏ qua mặt không có điểm mốc."""
    H, W = rgb.shape[:2]
    mask = np.zeros((H, W), np.uint8)

    def ell(c, ax, ay, ang):
        cv2.ellipse(mask, (int(c[0]), int(c[1])),
                    (int(max(2, ax)), int(max(2, ay))), ang, 0, 360, 255, -1)

    for fa in faces:
        if fa.get('lms') is None:
            continue
        x, y, w, h = fa['box']
        reye, leye, nose, rm, lmth = [fa['lms'][i] for i in range(5)]
        ev = leye - reye
        eye_d = float(np.hypot(ev[0], ev[1])) or (w * 0.4)
        up = np.array([ev[1], -ev[0]], np.float32)
        up = up / (np.hypot(up[0], up[1]) or 1.0)
        ang = float(np.degrees(np.arctan2(ev[1], ev[0])))
        emid = (reye + leye) / 2.0
        # MẮT + LÔNG MÀY (lông mày = nới LÊN theo 'up')
        ell(reye, eye_d * 0.34, eye_d * 0.24, ang)
        ell(leye, eye_d * 0.34, eye_d * 0.24, ang)
        ell(reye + up * eye_d * 0.42, eye_d * 0.36, eye_d * 0.18, ang)
        ell(leye + up * eye_d * 0.42, eye_d * 0.36, eye_d * 0.18, ang)
        # MŨI (từ giữa 2 mắt xuống chóp mũi + cánh mũi)
        ell((emid + nose) / 2.0, eye_d * 0.30, eye_d * 0.55, ang)
        ell(nose, eye_d * 0.28, eye_d * 0.24, ang)
        # MIỆNG
        mmid = (rm + lmth) / 2.0
        mw = float(np.hypot((lmth - rm)[0], (lmth - rm)[1])) or eye_d * 0.8
        ell(mmid, mw * 0.78, eye_d * 0.32, ang)
        # TAI (xấp xỉ — YuNet không có điểm mốc tai): bbox CHỈ ôm sát mặt nên kéo tâm
        # VÀO TRONG cho ellipse nằm trên mép mặt/chân tóc (đừng lấn nền). CHỈ vẽ khi
        # mặt gần CHÍNH DIỆN (góc nghiêng nhỏ) — mặt nghiêng thì mép bbox là nền.
        if abs(ang) < 18:
            ey = int((reye[1] + leye[1]) / 2.0)
            ell((x + w * 0.08, ey), w * 0.08, h * 0.14, 0)
            ell((x + w * 0.92, ey), w * 0.08, h * 0.14, 0)
    return mask if mask.any() else None


def detect_faces(rgb):
    """ĐIỂM VÀO DUY NHẤT dò mặt: YuNet (kèm 5 điểm mốc) -> lùi Haar (chỉ bbox). Trả
    list dict {box:(x,y,w,h), lms:(5,2)|None, score}. [] nếu tắt/không thấy/lỗi.
    Dò 1 LẦN rồi tái dùng cho cả: cắt vùng mặt (refine) VÀ mask bảo vệ ngũ quan."""
    try:
        if _OFF or rgb is None or rgb.ndim != 3:
            return []
        yf = _yunet_faces(rgb)
        if yf:
            return yf
        # Lùi Haar (không có điểm mốc) — vẫn đủ để cắt refine vùng mặt.
        H, W = rgb.shape[:2]
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        scale = 1.0
        if max(H, W) > 900:
            scale = 900.0 / max(H, W)
            gray = cv2.resize(gray, (int(W * scale), int(H * scale)),
                              interpolation=cv2.INTER_AREA)
        faces = _detect_faces(cv2.equalizeHist(gray))
        inv = 1.0 / scale
        boxes = [(int(x * inv), int(y * inv), int(w * inv), int(h * inv))
                 for (x, y, w, h) in faces]
        # GIỚI HẠN số mặt: Haar dễ báo NHẦM trên ảnh nhiều texture (lá, vải, đám đông)
        # -> mỗi bbox kéo theo 1 lần lượng tử cục bộ (nặng). Giữ tối đa 4 mặt TO nhất.
        boxes.sort(key=lambda b: -b[2] * b[3])
        return [{'box': b, 'lms': None, 'score': 0.0} for b in boxes[:4]]
    except Exception:
        return []


def detect_face_boxes(rgb):
    """List (x,y,w,h) khuôn mặt full-res. [] nếu không thấy/lỗi. (Tiện ích mỏng quanh
    detect_faces — giữ cho tương thích nơi chỉ cần bbox.)"""
    return [f['box'] for f in detect_faces(rgb)]


def scale_faces(faces, k):
    """Nhân toạ độ bbox + điểm mốc theo hệ số k (vd 1x -> 2x cho ảnh thiết kế). lms
    None thì giữ None. Dùng để tái dùng kết quả dò 1 lần cho ảnh ở độ phân giải khác."""
    out = []
    for f in faces:
        x, y, w, h = f['box']
        out.append({
            'box': (int(x * k), int(y * k), int(w * k), int(h * k)),
            'lms': (f['lms'] * k) if f.get('lms') is not None else None,
            'score': f.get('score', 0.0)})
    return out


def subject_crop_box(rgb, min_face_frac=0.004, max_face_frac=0.05, aspect=0.8, max_faces=3):
    """Khung CẮT 'tự ZOOM vào (các) người' cho ảnh CHÂN DUNG: ảnh chụp xa mặt rất nhỏ
    -> hệ thống hạ ảnh về ~1400px nên mặt còn ÍT pixel (mất nét). Cắt sát người -> dồn
    'ngân sách' độ phân giải cho người -> mặt NÉT hơn (chỉ thật sự lợi khi ảnh GỐC to).
    HỖ TRỢ 1..max_faces người (vd cặp đôi cõng nhau) — cắt quanh BAO của TẤT CẢ mặt.
    Trả (x0,y0,x1,y1) hoặc None nếu KHÔNG nên cắt:
      - tắt / lỗi / 0 mặt / > max_faces mặt (đám đông -> để yên);
      - có mặt dò KHÔNG chắc (Haar/không điểm mốc) -> không tự recompose;
      - có mặt quá nhỏ (người phụ/nền) hoặc đã to (cận) -> để yên;
      - khung cắt không nhỏ hơn ảnh đáng kể (>85% diện tích) -> cắt vô ích.
    Cắt BÁN THÂN dọc: chừa tóc/đầu trên, lấy tới quá ngực dưới, rộng ôm vai; bám tỉ lệ
    dọc 'aspect' (4:5 cho khổ in dọc); kẹp trong biên ảnh."""
    try:
        if _OFF or rgb is None or rgb.ndim != 3:
            return None
        faces = detect_faces(rgb)
        if not faces or len(faces) > max_faces:    # 0 mặt / đám đông -> không tự cắt
            return None
        if any(f.get('lms') is None for f in faces):   # PHẢI dò chắc (YuNet điểm mốc) HẾT
            return None
        H, W = rgb.shape[:2]
        bxs = [f['box'] for f in faces]
        for (x, y, w, h) in bxs:                   # mọi mặt phải nhỏ hợp lý (không có mặt cận)
            ff = w * h / float(W * H)
            if ff < min_face_frac or ff > max_face_frac:
                return None
        ux0 = min(b[0] for b in bxs)
        uy0 = min(b[1] for b in bxs)
        ux1 = max(b[0] + b[2] for b in bxs)
        uy1 = max(b[1] + b[3] for b in bxs)
        fw = max(b[2] for b in bxs)
        fh = max(b[3] for b in bxs)
        # PHẢI là MỘT nhóm chủ thể gắn kết (cặp đôi/ôm nhau), KHÔNG phải 2 người đứng
        # xa hay 2 mặt tình cờ trong cảnh: (a) trải NGANG không quá ~4 bề rộng mặt;
        # (b) các mặt CỠ gần nhau (khác nhiều = khác khoảng cách -> không cùng nhóm).
        if len(bxs) > 1:
            if (ux1 - ux0) > 4.0 * fw:
                return None
            areas = [b[2] * b[3] for b in bxs]
            if max(areas) > 4.0 * max(1, min(areas)):
                return None
        cx = (ux0 + ux1) / 2.0
        top = uy0 - 0.9 * fh                       # chừa tóc/đỉnh đầu (mặt trên cùng)
        bot = uy1 + 3.8 * fh                       # xuống quá ngực (bán thân)
        ch = bot - top
        cw = max((ux1 - ux0) + 2.6 * fw, ch * aspect)   # ôm hết mặt + vai; tối thiểu tỉ lệ dọc
        x0 = max(0, int(cx - cw / 2.0))
        x1 = min(W, int(cx + cw / 2.0))
        y0 = max(0, int(top))
        y1 = min(H, int(bot))
        if x1 - x0 < 16 or y1 - y0 < 16:
            return None
        if (x1 - x0) * (y1 - y0) > 0.85 * W * H:   # gần bằng ảnh -> bỏ (vô ích)
            return None
        return (x0, y0, x1, y1)
    except Exception:
        return None


def feature_protect_mask(rgb, faces=None):
    """Mask 0/255 (HxW) bảo vệ ngũ quan của ảnh CHÂN DUNG. None nếu tắt / không thấy
    mặt / lỗi. faces: kết quả detect_faces ĐÃ DÒ SẴN (toạ độ KHỚP rgb này) -> KHỎI chạy
    lại YuNet (dùng cho luồng đã dò 1 lần). rgb: mảng uint8 HxWx3 (RGB)."""
    if _OFF:
        return None
    try:
        if rgb is None or rgb.ndim != 3:
            return None
        m = _mediapipe_mask(rgb)                 # tốt nhất nếu CÀI mediapipe
        if m is None and faces:                  # dùng điểm mốc ĐÃ DÒ -> khỏi chạy lại
            if any(f.get('lms') is not None for f in faces):
                m = _landmark_mask(rgb, faces)
        if m is None:
            yf = _yunet_faces(rgb)               # chưa có -> tự dò DNN + điểm mốc
            if yf:
                m = _landmark_mask(rgb, yf)
        if m is None:
            m = _haar_mask(rgb)                  # lùi cuối: Haar + suy luận hình học
        if m is None or not m.any():
            return None
        # Nở nhẹ cho liền nét + chừa lề an toàn quanh ngũ quan.
        k = np.ones((3, 3), np.uint8)
        m = cv2.dilate(m, k, iterations=1)
        return m
    except Exception:
        return None
