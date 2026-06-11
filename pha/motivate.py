# -*- coding: utf-8 -*-
"""Câu truyền động lực hiển thị khi nhân viên bấm VÀO LÀM — đổi mỗi ngày, kèm emoji dễ thương."""

QUOTES = [
    ("🌞", "Chào buổi sáng! Một ngày mới rực rỡ đang chờ bạn toả sáng."),
    ("💪", "Cố lên nào! Mỗi nét cọ hôm nay là một bước tới thành công."),
    ("🎨", "Hôm nay mình lại tô màu cho cuộc sống thêm tươi nhé!"),
    ("✨", "Bạn làm được mà! Tin vào đôi tay khéo léo của mình."),
    ("🌈", "Sau cơn mưa trời lại sáng — cứ vui vẻ mà làm việc thôi!"),
    ("🚀", "Bắt đầu ngày mới đầy năng lượng, bứt phá nào!"),
    ("🌻", "Nụ cười là màu đẹp nhất — cười lên một cái nào!"),
    ("☕", "Nhâm nhi ngụm cà phê, hít thở thật sâu rồi chiến thôi!"),
    ("🐱", "Chăm chỉ một chút, mèo con cũng tự hào về bạn đó!"),
    ("🌟", "Việc nhỏ làm tốt, việc lớn ắt thành. Cứ từ từ mà chắc!"),
    ("🍀", "Chúc bạn một ngày may mắn và thật nhiều niềm vui!"),
    ("🔥", "Giữ lửa đam mê, hôm nay sẽ là ngày tuyệt vời!"),
    ("🌸", "Nhẹ nhàng thôi, làm bằng cả trái tim là đẹp nhất."),
    ("🦋", "Kiên nhẫn một chút, thành quả sẽ hoá thành điều diệu kỳ."),
    ("🎯", "Tập trung vào hôm nay — bạn đang làm rất tốt rồi!"),
    ("🌼", "Mỗi ngày đi làm là một ngày gieo hạt cho tương lai."),
    ("💖", "Cảm ơn bạn đã chăm chỉ — bạn quý giá lắm đó!"),
    ("🐝", "Chăm như ong, ngày hôm nay sẽ ngọt như mật!"),
    ("🌅", "Ngày mới, cơ hội mới. Hít một hơi và bắt đầu nào!"),
    ("🎁", "Mỗi sản phẩm bạn làm là một món quà cho khách hàng."),
    ("⭐", "Bạn là ngôi sao của xưởng hôm nay đấy!"),
    ("🌺", "Làm việc vui vẻ, hết ngày về nhà thật an yên nhé."),
    ("🍩", "Cố gắng buổi sáng, phần thưởng ngọt ngào đang chờ!"),
    ("🐬", "Thả lỏng vai, mỉm cười và làm điều mình giỏi nhất!"),
    ("🌷", "Chậm mà chắc, đẹp mà bền — đó là phong cách của bạn."),
    ("🎉", "Một ngày nữa để tự hào về chính mình. Quẩy lên!"),
    ("🌙", "Hôm qua đã cố gắng rồi, hôm nay mình tiếp tục toả sáng!"),
    ("🧡", "Đồng đội ở bên, mọi việc đều dễ dàng hơn. Cùng cố lên!"),
    ("🐧", "Bước từng bước nhỏ, đích đến sẽ rất gần thôi!"),
    ("🌿", "Hít thở không khí trong lành, tinh thần sảng khoái vào việc!"),
    ("💫", "Tin đi, hôm nay bạn sẽ làm được điều tuyệt vời!"),
]


def quote_for(date):
    """Trả (emoji, câu) theo NGÀY — cùng ngày mọi người thấy giống nhau, mỗi ngày đổi 1 câu."""
    try:
        idx = date.toordinal() % len(QUOTES)
    except Exception:
        idx = 0
    e, t = QUOTES[idx]
    return {'emoji': e, 'text': t}
