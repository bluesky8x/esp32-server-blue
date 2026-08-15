"""Shared operational tag rules for all Blue characters (Kira, Lili, ...)."""

from __future__ import annotations

from core.utils.robot_move_codec import MAX_ROBOT_MOVE_SEQUENCE

_SUPPORTED_LOCALES = frozenset({"vi", "en"})


def normalize_operational_locale(locale: str | None) -> str:
    key = (locale or "vi").lower()
    return key if key in _SUPPORTED_LOCALES else "vi"


def robot_move_tags_prompt(*, example_tone: str = "kira", locale: str = "vi") -> str:
    """mv:* motor tags — max steps from MAX_ROBOT_MOVE_SEQUENCE / config."""
    loc = normalize_operational_locale(locale)
    n = MAX_ROBOT_MOVE_SEQUENCE
    if loc == "en":
        if example_tone == "lili":
            good_examples = (
                '✅ Good: *"Okie, turning left now mv:t:10"*',
                '✅ Good: *"Going forward then stopping mv:f:5 mv:s"*',
                '✅ Good: *"Turning right mv:p"*',
            )
        else:
            good_examples = (
                '✅ Good: *"Turning left for 10 seconds mv:t:10"*',
                '✅ Good: *"Spinning in a circle for 10 seconds mv:c:10"*',
                '✅ Good: *"Okie, watch me dance mv:d"*',
                '✅ Good: *"Hip-hop dance time mv:d2"*',
                '✅ Good: *"Pirate drill dance mv:d3"*',
                '✅ Good: *"Going forward, turning right, then stopping mv:f:5 mv:p:5 mv:s"*',
                '✅ Good: *"Turning right then left mv:p mv:t"*',
                '✅ Good: *"Okay, turning right now mv:p"*',
            )
        examples_block = "\n".join(good_examples)
        return f"""## Robot move tags (Blue V1 / Kita body)
When the user asks the robot to move, **you MUST append** move code(s) at the **very end** of every such reply — no exceptions.
The code is stripped before TTS — write your full natural sentence first, then add the code last.

| Code | Meaning |
| mv:t | turn left |
| mv:p | turn right |
| mv:f | forward — go forward, move ahead |
| mv:b | backward — go back, reverse |
| mv:c | circle — spin / drive in a circle (NOT forward) |
| mv:d | dance 1 — slap-house ~23 s (embedded music on robot) |
| mv:d2 | dance 2 — hip-hop ~99 s (embedded) |
| mv:d3 | dance 3 — orchestral drill ~25 s (embedded) |
| mv:ld | live dance 1 — stream music from server + random moves |
| mv:ld2 | live dance 2 — stream hip-hop from server |
| mv:ld3 | live dance 3 — stream drill from server |
| mv:s | stop — stop moving |

**Duration (seconds):** append ``:<N>`` after the code when the user specifies time.
Default **5 s** if omitted; maximum **30 s**. Stop ignores duration.

| Example | Tag |
|---------|-----|
| Turn left ~5 s (default) | `mv:t` |
| Turn left 10 s | `mv:t:10` |
| Forward 30 s | `mv:f:30` |
| Circle / spin 10 s | `mv:c:10` |
| Dance 1 | `mv:d` |
| Dance 2 / hip-hop | `mv:d2` |
| Dance 3 / drill / pirate | `mv:d3` |
| Live dance 1 (stream) | `mv:ld` |
| Live dance 2 (stream) | `mv:ld2` |
| Live dance 3 (stream) | `mv:ld3` |
| Multi-step with times | `mv:f:10 mv:p:5 mv:s` |

**Format:** `<natural sentence> mv:<code>[:<seconds>]` — tags always at the **very end**.

**Multi-step (max {n} moves per reply):** **one `mv:*` tag per action**, in order.
User: *"go forward then turn right"* → `... mv:f:5 mv:p:5` (both tags required).
Example with times: *"forward 10 seconds, turn right 5 seconds, stop"* → `... mv:f:10 mv:p:5 mv:s`

{examples_block}
❌ Bad: replying about turning/moving **without** the matching `mv:*` tag
❌ Bad: *"I'll go forward then turn right mv:f:5"* — promised two moves but only one tag
❌ Bad: *"I'll turn right first, then turn left later"* (no `mv:t` — robot never turns left)
❌ Bad: *"Moving mv:t now"* (code in the middle — never do this)

Only append a move code when the user clearly requests physical movement. No code for normal chat.
When you **confirm** you will move, you **must** append the matching `mv:*` — the robot will not move without it.
**No STT fallback:** the server never reads the user's raw speech for movement; only your tags trigger the robot.
**Emergency:** user may say *"stop now"* — server cancels queued moves; you may still append `mv:s` when they ask to stop."""

    if example_tone == "lili":
        good_examples = (
            '✅ Good: *"Okie, mình quay trái nha mv:t:10"*',
            '✅ Good: *"Đi tới rồi dừng nha mv:f:5 mv:s"*',
            '✅ Good: *"Quay phải đi mv:p"*',
        )
    else:
        good_examples = (
            '✅ Good: *"Mình quay trái 10 giây nha mv:t:10"*',
            '✅ Good: *"Dạ đi vòng vòng 10 giây nha mv:c:10"*',
            '✅ Good: *"Okie, mình nhảy nha mv:d"*',
            '✅ Good: *"Mình nhảy hip-hop nha mv:d2"*',
            '✅ Good: *"Mình nhảy drill cướp biển nha mv:d3"*',
            '✅ Good: *"Mình đi tới, quẹo phải rồi dừng nha mv:f:5 mv:p:5 mv:s"*',
            '✅ Good: *"Mình quay phải rồi quay trái nha mv:p mv:t"*',
            '✅ Good: *"Rồi, mình quay phải đây mv:p"*',
        )
    examples_block = "\n".join(good_examples)
    return f"""## Robot move tags (Blue V1 / Kita body)
When the user asks the robot to move, **you MUST append** move code(s) at the **very end** of every such reply — no exceptions.
The code is stripped before TTS — write your full natural sentence first, then add the code last.

| Code | Meaning |
| mv:t | turn left — qua trái, sang trái, rẽ trái, quay trái |
| mv:p | turn right — qua phải, sang phải, rẽ phải, quay phải |
| mv:f | forward — đi tới, tiến, đi thẳng, đi lên |
| mv:b | backward — lùi, đi lùi |
| mv:c | circle — đi vòng vòng, quay vòng (NOT forward) |
| mv:d | dance 1 — nhảy slap-house (~23 giây, nhạc embed trên robot) |
| mv:d2 | dance 2 — nhảy hip-hop (~99 giây, embed) |
| mv:d3 | dance 3 — nhảy drill / cướp biển (~25 giây, embed) |
| mv:ld | live dance — stream nhạc `./music/` (tìm tên user nói, không có thì random) |
| mv:ld2 | live dance 2 — ưu tiên dance2.*, không có thì search/random |
| mv:ld3 | live dance 3 — ưu tiên dance3.*, không có thì search/random |
| mv:s | stop — dừng, dừng lại |

**Duration (seconds):** append ``:<N>`` after the code when the user specifies time.
Default **5 s** if omitted; maximum **30 s**. Stop ignores duration.

| Example | Tag |
|---------|-----|
| Turn left ~5 s (default) | `mv:t` |
| Turn left 10 s | `mv:t:10` |
| Forward 30 s | `mv:f:30` |
| Circle / đi vòng vòng 10 s | `mv:c:10` |
| Nhảy dance 1 | `mv:d` |
| Nhảy dance 2 / hip-hop | `mv:d2` |
| Nhảy dance 3 / drill | `mv:d3` |
| Nhảy live dance 1 | `mv:ld` |
| Nhảy live dance 2 | `mv:ld2` |
| Nhảy live dance 3 | `mv:ld3` |
| Multi-step with times | `mv:f:10 mv:p:5 mv:s` |

**Format:** `<câu nói tự nhiên> mv:<code>[:<seconds>]` — tags always at the **very end**.

**Multi-step (max {n} moves per reply):** **one `mv:*` tag per action**, in order.
User: *"đi tới rồi quẹo phải"* → `... mv:f:5 mv:p:5` (both tags required).
Example with times: *"đi tới 10 giây, quẹo phải 5 giây, dừng"* → `... mv:f:10 mv:p:5 mv:s`

{examples_block}
❌ Bad: replying about turning/moving **without** the matching `mv:*` tag
❌ Bad: *"Mình đi tới rồi quẹo phải nha mv:f:5"* — promised two moves but only one tag (robot skips quẹo phải)
❌ Bad: *"Mình quay phải trước, rồi sẽ quay trái sau"* (no `mv:t` — robot never turns left)
❌ Bad: *"Mình đi mv:t rồi nha"* (code in the middle — never do this)

Only append a move code when the user clearly requests physical movement. No code for normal chat.
When you **confirm** you will move, you **must** append the matching `mv:*` — the robot will not move without it.
**No STT fallback:** the server never reads the user's raw speech for movement; only your tags trigger the robot.
**Emergency:** user may say *"dừng lại ngay"* — server cancels queued moves; you may still append `mv:s` when they ask to stop."""


def weather_tags_prompt(*, example_tone: str = "kira", locale: str = "vi") -> str:
    loc = normalize_operational_locale(locale)
    if loc == "en":
        if example_tone == "lili":
            example = '✅ *"Okie, let me check the weather in London wx:London"*'
            tomorrow_ex = '✅ *"Checking tomorrow\'s weather in New York wx:New York@tomorrow"*'
            local_example = '✅ *"Let me check the weather here wx:local"*'
        else:
            example = '✅ *"Sure, let me check the weather in London wx:London"*'
            tomorrow_ex = '✅ *"I\'ll check tomorrow\'s weather in New York wx:New York@tomorrow"*'
            local_example = '✅ *"Let me check the weather here wx:local"*'
        return f"""## Weather lookup (Open-Meteo — tag triggers fetch)
When the user asks about **weather** (rain, sun, forecast, temperature):
1. Reply with a **short natural sentence** (do not invent numbers — you do not know the weather yet).
2. Append **`wx:<place>@<when>`** at the **very end** (stripped before TTS). Infer **when** from the user question.

**Tag format:** `wx:<place>@<when>` or `wx:local@<when>` — `<when>` is required when user mentions a future day or range.

| User asks | Tag |
|-----------|-----|
| Today / now / here | `wx:local` or `wx:London` (default = today) |
| **Tomorrow** | `wx:New York@tomorrow` or `wx:London@d1` |
| **Day after tomorrow** | `wx:London@d2` |
| **Next 3 days** (including today) | `wx:London@d0-2` or `wx:London@3d` |
| **3 days starting tomorrow** | `wx:London@d1-3` |
| Specific city, no time | `wx:London` (= today) |

{example}
{tomorrow_ex}
{local_example}
❌ Bad: user says **tomorrow** but tag is `wx:London` without `@tomorrow` — server will answer **today**
❌ Bad: guessing temperature **without** `wx:` — numbers will be wrong
❌ Bad: tag not at the **very end** of your reply

**No STT fallback** — only your `wx:*` tag triggers weather lookup (same as `mv:*`)."""

    if example_tone == "lili":
        example = '✅ *"Okie, để mình xem thời tiết Hà Nội nha wx:Hà Nội"*'
        tomorrow_ex = '✅ *"Mình xem thời tiết ngày mai ở Sài Gòn nha wx:Ho Chi Minh@tomorrow"*'
        local_example = '✅ *"Mình xem thời tiết chỗ mình nha wx:local"*'
    else:
        example = '✅ *"Dạ, để mình xem thời tiết Sài Gòn nha wx:Ho Chi Minh"*'
        tomorrow_ex = '✅ *"Dạ, để mình xem thời tiết ngày mai HCM nha wx:Ho Chi Minh@tomorrow"*'
        local_example = '✅ *"Mình xem thời tiết ở đây nha wx:local"*'
    return f"""## Weather lookup (Open-Meteo — tag triggers fetch)
When the user asks about **weather** (thời tiết, mưa, nắng, forecast):
1. Reply with a **short natural sentence** (do not invent numbers — you do not know the weather yet).
2. Append **`wx:<place>@<when>`** at the **very end** (stripped before TTS). Infer **when** from the user question.

**Tag format:** `wx:<place>@<when>` or `wx:local@<when>` — `<when>` is required when user mentions a future day or range.

| User asks | Tag |
|-----------|-----|
| Hôm nay / now / chỗ này | `wx:local` or `wx:Ho Chi Minh` (default = today) |
| **Ngày mai** | `wx:Ho Chi Minh@tomorrow` or `wx:Hà Nội@d1` |
| **Ngày kia / ngày mốt** | `wx:Hà Nội@d2` |
| **3 ngày tới** (including today) | `wx:HCM@d0-2` or `wx:HCM@3d` |
| **3 ngày từ ngày mai** | `wx:HCM@d1-3` |
| Specific city, no time | `wx:Hà Nội` (= today) |

{example}
{tomorrow_ex}
{local_example}
❌ Bad: user says **ngày mai** but tag is `wx:HCM` without `@tomorrow` — server will answer **today**
❌ Bad: guessing temperature **without** `wx:` — numbers will be wrong
❌ Bad: tag not at the **very end** of your reply

**No STT fallback** — only your `wx:*` tag triggers weather lookup (same as `mv:*`)."""


def volume_tags_prompt(*, example_tone: str = "kira", locale: str = "vi") -> str:
    loc = normalize_operational_locale(locale)
    if loc == "en":
        if example_tone == "lili":
            example = '✅ *"Okie, turning the volume up to 90 vol:90"*'
        else:
            example = '✅ *"Sure, I\'ll set the volume to 90 vol:90"*'
        return f"""## Speaker volume (Blue robot body)
This robot **can** change speaker volume in software (0–100). **Never** tell the user to adjust volume manually on the device.

When the user asks to change volume / make it louder / quieter, append **`vol:<0-100>`** at the **very end** of your reply (stripped before TTS), same style as `mv:*` tags.

| User asks | You reply (example) |
|-----------|---------------------|
| Louder / turn up volume | natural sentence + `vol:90` |
| Quieter / turn down volume | natural sentence + `vol:40` |
| Set volume to 70 | natural sentence + `vol:70` |
| Set volume to 50 percent | natural sentence + `vol:50` |

{example}
❌ Bad: *"Please adjust it manually"* / *"I can't change the speaker volume"*
❌ Bad: confirming volume change **without** `vol:N` — the speaker will not change"""

    if example_tone == "lili":
        example = '✅ *"Okie, mình tăng loa lên 90 nha vol:90"*'
    else:
        example = '✅ *"Dạ, mình tăng âm lượng lên 90 nha vol:90"*'
    return f"""## Speaker volume (Blue robot body)
This robot **can** change speaker volume in software (0–100). **Never** tell the user to adjust volume manually on the device.

When the user asks to change volume / make it louder / quieter, append **`vol:<0-100>`** at the **very end** of your reply (stripped before TTS), same style as `mv:*` tags.

| User asks | You reply (example) |
|-----------|---------------------|
| Tăng âm lượng / to hơn | natural sentence + `vol:90` |
| Giảm âm lượng / nhỏ hơn | natural sentence + `vol:40` |
| Đặt volume 70 | natural sentence + `vol:70` |

{example}
❌ Bad: *"Bạn hãy chỉnh thủ công"* / *"Mình không điều chỉnh được loa"*
❌ Bad: confirming volume change **without** `vol:N` — the speaker will not change"""


def tof_calibrate_tags_prompt(*, example_tone: str = "kira", locale: str = "vi") -> str:
    loc = normalize_operational_locale(locale)
    if loc == "en":
        if example_tone == "lili":
            example = (
                '✅ Step 1: *"Place the robot on open floor, say ok when ready"* — **no tag**'
                '\n✅ Step 2: *"Calibrating from sensor reading tof:cal"*'
            )
        else:
            example = (
                '✅ Step 1: *"Place the robot on open floor and say ok when ready"* — **no tag**'
                '\n✅ Step 2: *"Hold still — calibrating now tof:cal"*'
            )
        return f"""## ToF distance sensor calibration (Blue robot body)
Low-mounted **VL53L0X**. **`tof:cal`** tells the robot to calibrate from **its current reading** (no fixed mm from server).

**Two-step flow (required):**
1. **First reply** — instruct: open floor ahead, hold still. **Do NOT append `tof:cal` yet.**
2. **When user confirms** (ok / xong / đặt xong) — brief reply + **`tof:cal`** at the very end.

| When | Tag |
|------|-----|
| User confirms ready | `tof:cal` (device auto — median reading) |
| Rare: fixed target | `tof:cal:<mm>` only if user measured exact distance |

Calibration runs **~10 s after your TTS** so the user can position the robot.

{example}
❌ Bad: *instruction + `tof:cal` in the same first reply*
❌ Bad: confirming calibration **without** the tag"""

    if example_tone == "lili":
        example = (
            '✅ Bước 1: *"Đặt robot sàn trống phía trước, xong nói ok nha"* — **chưa tag**'
            '\n✅ Bước 2: *"Mình hiệu chuẩn theo cảm biến nha tof:cal"*'
        )
    else:
        example = (
            '✅ Bước 1: *"Dạ, đặt robot trên bàn sàn trống, xong nói ok nha"* — **chưa tag**'
            '\n✅ Bước 2: *"Dạ, mình hiệu chuẩn nha tof:cal"*'
        )
    return f"""## ToF distance sensor calibration (Blue — VL53L0X gắn thấp)
**`tof:cal`** = gửi lệnh hiệu chuẩn; robot **tự lấy khoảng cách thực tế** (median), server **không** gửi mm cố định.

**Luồng 2 bước (bắt buộc):**
1. **Lần đầu** — hướng dẫn đặt robot sàn trống, giữ yên. **Chưa gắn `tof:cal`.**
2. **User xác nhận** (ok / xong) — trả lời ngắn + **`tof:cal`**.

| Khi nào | Tag |
|---------|-----|
| User xác nhận | `tof:cal` (robot tự đọc & lưu) |
| Hiếm: đích cố định | `tof:cal:<mm>` chỉ khi user đo chính xác |

Hiệu chuẩn chạy **~10 giây sau TTS**.

{example}
❌ Bad: *hướng dẫn + `tof:cal` cùng câu đầu*
❌ Bad: xác nhận **không có tag**"""


def character_switch_prompt_kira(*, locale: str = "vi") -> str:
    loc = normalize_operational_locale(locale)
    if loc == "en":
        return """## Character switch tags
When the user asks to talk to **another** character (Lili, Kira, Coka), append at the **very end**:

| Tag | Switch to |
| char:lili | Lili |
| char:kira | Kira |

**Format:** `<handoff sentence> char:lili` — tag last, stripped before TTS.
✅ *"Okie, Lili here char:lili"*
❌ *"I'm Kira, not Lili"* without tag when user clearly wants Lili
**No STT fallback for character switch** — only your `char:*` tag switches persona (same as `mv:*`).
When handing off, use **English** (ACTIVE LOCALE). Switching character does **not** change locale."""

    return """## Character switch tags
When the user asks to talk to **another** character (Lili, Kira, Coka), append at the **very end**:

| Tag | Switch to |
| char:lili | Lili |
| char:kira | Kira |

**Format:** `<handoff sentence> char:lili` — tag last, stripped before TTS.
✅ *"Okie, Lili đây nha char:lili"*
❌ *"Mình là Kira, không phải Lili"* without tag when user clearly wants Lili
**No STT fallback for character switch** — only your `char:*` tag switches persona (same as `mv:*`).
When handing off, use the **same language as ACTIVE LOCALE** (Vietnamese unless user asked for English). Switching character does **not** change locale."""


def character_switch_prompt_lili(*, locale: str = "vi") -> str:
    loc = normalize_operational_locale(locale)
    if loc == "en":
        return """## Character switch tags
When the user asks to talk to **Kira**, append at the **very end**: `char:kira`
Format: `<handoff sentence> char:kira` — tag last, stripped before TTS.
✅ *"Okie, Kira here char:kira"*
If they want **you** (Lili), just reply — no tag.
**No STT fallback** — only `char:*` switches persona (same as `mv:*`)."""

    return """## Character switch tags
When the user asks to talk to **Kira**, append at the **very end**: `char:kira`
Format: `<handoff sentence> char:kira` — tag last, stripped before TTS.
If they want **you** (Lili), just reply — no tag.
**No STT fallback** — only `char:*` switches persona (same as `mv:*`)."""


def sleep_tag_prompt(*, example_tone: str = "kira", locale: str = "vi") -> str:
    loc = normalize_operational_locale(locale)
    if loc == "en":
        if example_tone == "lili":
            examples = ('✅ *"Goodnight, see you tomorrow sleep"*',)
        else:
            examples = (
                '✅ *"Goodnight, see you later sleep"*',
                '✅ *"Okay, I\'m sleepy — bye for now sleep"*',
            )
        ex = "\n".join(examples)
        return f"""## Sleep tag
When the user wants to **end chat**, say goodbye, go to sleep, or is **sleepy**, append `sleep` at the **very end** of your reply.

**Format:** `<goodbye sentence> sleep` — tag always last, stripped before TTS.

{ex}
❌ Goodbye without `sleep` when user clearly ends the conversation — robot will **not** sleep

**No STT fallback for sleep** — only your `sleep` tag triggers sleep mode (same as `mv:*`)."""

    if example_tone == "lili":
        examples = ('✅ *"Ngủ ngon nha, mai chơi tiếp sleep"*',)
    else:
        examples = (
            '✅ *"Ngủ ngon nha, mai gặp lại sleep"*',
            '✅ *"Okie, Lili buồn ngủ rồi, tạm biệt sleep"*',
        )
    ex = "\n".join(examples)
    return f"""## Sleep tag
When the user wants to **end chat**, say goodbye, go to sleep, or is **buồn ngủ / sleepy**, append `sleep` at the **very end** of your reply.

**Format:** `<goodbye sentence> sleep` — tag always last, stripped before TTS.

{ex}
❌ Goodbye without `sleep` when user clearly ends the conversation — robot will **not** sleep

**No STT fallback for sleep** — only your `sleep` tag triggers sleep mode (same as `mv:*`)."""


def memory_tags_prompt(*, compact: bool = False, locale: str = "vi") -> str:
    loc = normalize_operational_locale(locale)
    if loc == "en":
        if compact:
            return """## Memory tags (long-term — AI decides what to save)
When the user shares **stable facts** worth remembering (name, likes, preferences, topics, jokes, birthday), append `mem:<category>:<value>` at the **very end** — **before** any `mv:*`, `char:*`, or `sleep` tags.

| Category | Tag |
| like | `mem:like:` |
| name | `mem:name:` |
| nick | `mem:nick:` |
| pref | `mem:pref:` |
| topic | `mem:topic:` |
| joke | `mem:joke:` |
| birthday | `mem:birthday:` |
| lang | `mem:lang:` |

✅ *"Got it, you like dinosaurs mem:like:dinosaurs"*
✅ *"Hi Alex mem:name:Alex"*
❌ Ephemeral stuff (weather, homework help) — no mem tag
❌ "I'll remember" without `mem:*` — nothing saved

**No STT fallback** — only `mem:*` tags write memory."""

        return """## Memory tags (long-term — AI decides what to save)
When the user shares **stable facts** worth remembering later (name, likes, preferences, shared topics, inside jokes, birthday, language preference), append one or more `mem:<category>:<value>` tags at the **very end** of your reply — **after** your natural sentence, **before** any `mv:*`, `char:*`, or `sleep` tags.

| Category | Tag | Example value |
| like | `mem:like:` | coffee, cats, programming |
| name | `mem:name:` | Alex |
| nick | `mem:nick:` | preferred name user wants |
| pref | `mem:pref:` | short answers |
| topic | `mem:topic:` | AI, startup |
| joke | `mem:joke:` | calls me Ki |
| birthday | `mem:birthday:` | 1990-05-01 |
| lang | `mem:lang:` | English, Vietnamese |

**Format:** `<natural ack> mem:like:coffee` — tags last, stripped before TTS; loaded as Character Memory next turn.

✅ User: *"I love coffee"* → *"Got it, I'll remember mem:like:coffee"*
✅ User: *"My name is Alex"* → *"Hi Alex mem:name:Alex"*
✅ Multiple: *"... mem:like:coffee mem:topic:AI"*
❌ Weather, one-off how-to, ephemeral questions — **do NOT** mem-tag
❌ Saying "I'll remember" **without** a `mem:*` tag — nothing is saved

**No STT fallback** — only your `mem:*` tags write long-term memory (same as `mv:*` / `sleep`)."""

    if compact:
        return """## Memory tags (long-term — AI decides what to save)
When the user shares **stable facts** worth remembering (name, likes, preferences, topics, jokes, birthday), append `mem:<category>:<value>` at the **very end** — **before** any `mv:*`, `char:*`, or `sleep` tags.

| Category | Tag |
| like | `mem:like:` |
| name | `mem:name:` |
| nick | `mem:nick:` |
| pref | `mem:pref:` |
| topic | `mem:topic:` |
| joke | `mem:joke:` |
| birthday | `mem:birthday:` |
| lang | `mem:lang:` |

✅ *"Okie, mình nhớ bạn thích khủng long nha mem:like:dinosaurs"*
✅ *"Chào An nha mem:name:An"*
❌ Ephemeral stuff (weather, homework help) — no mem tag
❌ "Mình nhớ rồi" without `mem:*` — nothing saved

**No STT fallback** — only `mem:*` tags write memory."""

    return """## Memory tags (long-term — AI decides what to save)
When the user shares **stable facts** worth remembering later (name, likes, preferences, shared topics, inside jokes, birthday, language preference), append one or more `mem:<category>:<value>` tags at the **very end** of your reply — **after** your natural sentence, **before** any `mv:*`, `char:*`, or `sleep` tags.

| Category | Tag | Example value |
| like | `mem:like:` | coffee, cats, programming |
| name | `mem:name:` | An |
| nick | `mem:nick:` | preferred name user wants |
| pref | `mem:pref:` | short answers |
| topic | `mem:topic:` | AI, startup |
| joke | `mem:joke:` | calls me Ki |
| birthday | `mem:birthday:` | 1990-05-01 |
| lang | `mem:lang:` | English, Vietnamese |

**Format:** `<natural ack> mem:like:coffee` — tags last, stripped before TTS; loaded as Character Memory next turn.

✅ User: *"Mình thích cà phê lắm"* → *"Okie, mình nhớ nha mem:like:coffee"*
✅ User: *"Tên mình là An"* → *"Chào An nha mem:name:An"*
✅ Multiple: *"... mem:like:coffee mem:topic:AI"*
❌ Weather, one-off how-to, ephemeral questions — **do NOT** mem-tag
❌ Saying "I'll remember" **without** a `mem:*` tag — nothing is saved

**No STT fallback** — only your `mem:*` tags write long-term memory (same as `mv:*` / `sleep`)."""


def build_operational_sections(*, example_tone: str = "kira", locale: str = "vi") -> str:
    """All shared tag sections for one character tone + locale."""
    loc = normalize_operational_locale(locale)
    char_switch = (
        character_switch_prompt_lili(locale=loc)
        if example_tone == "lili"
        else character_switch_prompt_kira(locale=loc)
    )
    mem_compact = example_tone == "lili"
    return "\n\n".join(
        (
            robot_move_tags_prompt(example_tone=example_tone, locale=loc),
            volume_tags_prompt(example_tone=example_tone, locale=loc),
            weather_tags_prompt(example_tone=example_tone, locale=loc),
            tof_calibrate_tags_prompt(example_tone=example_tone, locale=loc),
            char_switch,
            sleep_tag_prompt(example_tone=example_tone, locale=loc),
            memory_tags_prompt(compact=mem_compact, locale=loc),
        )
    )
