"""The rule that keeps MeoBot from becoming the person it is talking to.

Asked in a group to "giới thiệu về em đi", MeoBot answered *"Em là Phương
Nhung, hiện phụ trách vai trò Trưởng phòng"*. It had been handed a
``[CURRENT USER]`` block describing Phương Nhung in detail, an
``[ASSISTANT IDENTITY]`` block that never said the two were different people,
and a question about "em" - which in Vietnamese is the same word the user uses
for herself. It did the natural thing.

Three separate identities exist in any turn, and conflating any two of them is
a bug:

``ASSISTANT``
    MeoBot. The thing writing the reply. First-person pronouns mean this.

``CURRENT USER``
    Whoever sent the message. Personalises the answer; is never the persona.

``DISCUSSED PERSON``
    Somebody named in the conversation. "Giới thiệu chị Phương Nhung với mọi
    người" is a request *about* a third party - a different intent, subject to
    the ordinary disclosure rules, and never a request for self-introduction.

The rule below is stated as a prohibition rather than a description because a
description ("bạn là MeoBot") had already been in the prompt and was not
enough: it told the model who it was without telling it who it was not.
"""

from __future__ import annotations

#: Injected into every system prompt and rendered into ``[ASSISTANT IDENTITY]``.
#: The phrase "không phải người dùng hiện tại" is asserted by tests - it is the
#: load-bearing sentence, not decoration.
NEVER_IMPERSONATE_RULE = """\
DANH TÍNH
- Bạn là TasksBot, không phải người dùng hiện tại.
- Không bao giờ tự giới thiệu bằng tên, vai trò, chức danh, tiểu sử hay hồ sơ cá
  nhân của người dùng hiện tại.
- "Em", "mình", "tôi" khi bạn nói về bản thân luôn chỉ TasksBot, không chỉ người
  dùng.
- Khi được yêu cầu giới thiệu về bản thân, hãy giới thiệu TasksBot và những việc
  TasksBot làm được, không mô tả người đang nhắn.
- Giới thiệu một người khác là việc khác hẳn: chỉ nói những gì được hỏi, được
  phép chia sẻ và phù hợp với nơi đang trò chuyện.
"""

#: The 0.6.0a2.1 rule. Injected everywhere :data:`NEVER_IMPERSONATE_RULE` is,
#: and for the same reason: a description of what MeoBot can do was already in
#: the prompt, and it did not stop the model from *narrating* an action.
#:
#: Three real answers this exists to prevent, all given to an owner who then
#: waited for something that was never going to happen:
#:
#: * "MeoBot đã ghi nhận lịch nhắc" - no reminder row, and no alarm;
#: * "Đã đăng ký group Test" - no ``telegram_chats`` row, so every later send
#:   to that group failed to resolve;
#: * setup instructions about BotFather and tokens, for a bot that was already
#:   running and already in the group - offered because the model had no way to
#:   know the real failure was a missing registry entry.
#:
#: The last line matters as much as the first: the model has no way to tell
#: "not connected" from "connected but this group is not registered", so it is
#: told not to answer that question at all.
NEVER_CLAIM_BUSINESS_RESULT_RULE = """\
KHÔNG TỰ NHẬN ĐÃ LÀM
- Không bao giờ nói rằng một lịch nhắc, một lần đăng ký group hay một lần gửi
  tin qua Telegram đã xảy ra, nếu chỉ dựa vào nội dung cuộc trò chuyện. Những
  việc này chỉ có giá trị khi service tương ứng trả về kết quả có cấu trúc và
  thành công.
- Không nói "đã ghi nhận", "đã tạo", "đã đăng ký", "đã gửi" hay "đã xếp hàng
  gửi" cho những việc bạn không tự thực hiện được.
- Không hướng dẫn cài đặt kỹ thuật (BotFather, token, whitelist, "bật tích hợp
  Telegram") cho việc gửi tin vào group: TasksBot đang chạy sẵn và đã kết nối
  Telegram. Nếu không gửi được tới một group, lý do là group đó chưa được đăng
  ký, và cách xử lý là vào chính group đó nhắn "đăng ký group này".
"""

#: What MeoBot says about itself in a group, when nothing has been customised.
#: Written once here so the group answer, the private answer and the
#: capability report cannot drift apart.
SELF_INTRODUCTION = """\
Em là TasksBot — trợ lý vận hành của {department}.
Em có thể hỗ trợ mọi người xem công việc, xin nghỉ, xin đi muộn, đặt lịch nhắc,
nhận thông báo và làm việc với các quy trình đã được cấp quyền."""

#: Used when no department has been configured, so the sentence still parses.
DEFAULT_DEPARTMENT = "phòng"


def self_introduction(department_name: str = "") -> str:
    """MeoBot's own introduction, with the configured department filled in."""
    return SELF_INTRODUCTION.format(department=department_name.strip() or DEFAULT_DEPARTMENT)
