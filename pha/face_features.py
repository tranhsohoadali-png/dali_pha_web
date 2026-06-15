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


def feature_protect_mask(rgb):
    """Mask 0/255 (HxW) bảo vệ ngũ quan của ảnh CHÂN DUNG. None nếu tắt / không
    thấy mặt / lỗi. rgb: mảng uint8 HxWx3 (RGB)."""
    if _OFF:
        return None
    try:
        if rgb is None or rgb.ndim != 3:
            return None
        m = _mediapipe_mask(rgb)
        if m is None:
            m = _haar_mask(rgb)
        if m is None or not m.any():
            return None
        # Nở nhẹ cho liền nét + chừa lề an toàn quanh ngũ quan.
        k = np.ones((3, 3), np.uint8)
        m = cv2.dilate(m, k, iterations=1)
        return m
    except Exception:
        return None
