"""Shared operational tag rules for all Blue characters (Kira, Lili, ...)."""

from __future__ import annotations

from core.utils.robot_move_codec import MAX_ROBOT_MOVE_SEQUENCE


def robot_move_tags_prompt(*, example_tone: str = "kira") -> str:
    """mv:* motor tags — max steps from MAX_ROBOT_MOVE_SEQUENCE / config."""
    n = MAX_ROBOT_MOVE_SEQUENCE
    if example_tone == "lili":
        good_examples = (
            f'✅ Good: *"Okie, mình quay trái nha mv:t:10"*',
            f'✅ Good: *"Đi tới rồi dừng nha mv:f:5 mv:s"*',
            f'✅ Good: *"Quay phải đi mv:p"*',
        )
    else:
        good_examples = (
            f'✅ Good: *"Mình quay trái 10 giây nha mv:t:10"*',
            f'✅ Good: *"Dạ đi vòng vòng 10 giây nha mv:c:10"*',
            f'✅ Good: *"Mình đi tới, quẹo phải rồi dừng nha mv:f:5 mv:p:5 mv:s"*',
            f'✅ Good: *"Mình quay phải rồi quay trái nha mv:p mv:t"*',
            f'✅ Good: *"Rồi, mình quay phải đây mv:p"*',
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
| mv:s | stop — dừng, dừng lại |

**Duration (seconds):** append ``:<N>`` after the code when the user specifies time.
Default **5 s** if omitted; maximum **30 s**. Stop ignores duration.

| Example | Tag |
|---------|-----|
| Turn left ~5 s (default) | `mv:t` |
| Turn left 10 s | `mv:t:10` |
| Forward 30 s | `mv:f:30` |
| Circle / đi vòng vòng 10 s | `mv:c:10` |
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


def weather_tags_prompt(*, example_tone: str = "kira") -> str:
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


def volume_tags_prompt(*, example_tone: str = "kira") -> str:
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


def tof_calibrate_tags_prompt(*, example_tone: str = "kira") -> str:
    if example_tone == "lili":
        example = '✅ *"Okie, đặt robot trên sàn trống phía trước (~40 cm) rồi mình hiệu chuẩn nha tof:cal:400"*'
    else:
        example = '✅ *"Dạ, bạn đặt robot trên bàn sàn trống phía trước (~40 cm), mình hiệu chuẩn nha tof:cal:400"*'
    return f"""## ToF distance sensor calibration (Blue robot body)
This robot has a **VL53L0X** front distance sensor. Calibration saves a **safe travel distance** to flash.

When the user asks to **calibrate** the distance sensor / ToF / cảm biến khoảng cách:
1. Tell them to place the robot on **open floor** with clear space ahead (default **~40 cm / 400 mm** to the floor/wall ahead), hold still.
2. Append **`tof:cal`** or **`tof:cal:<mm>`** at the **very end** of your reply (stripped before TTS).
3. After calibrate, forward motion stops if distance becomes **much closer** (obstacle) or **much farther** (cliff) than the saved reference.

| User asks | Tag |
|-----------|-----|
| Hiệu chuẩn cảm biến (sàn trống ~40 cm) | `tof:cal` or `tof:cal:400` |
| Calibrate at 35 cm | `tof:cal:350` |

{example}
❌ Bad: *"Mình không hiệu chuẩn được"* / instruct manual steps **without** `tof:cal` — calibration will not run
❌ Bad: confirming calibration **without** the tag — robot will not calibrate or save"""


def character_switch_prompt_kira() -> str:
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


def character_switch_prompt_lili() -> str:
    return """## Character switch tags
When the user asks to talk to **Kira**, append at the **very end**: `char:kira`
Format: `<handoff sentence> char:kira` — tag last, stripped before TTS.
If they want **you** (Lili), just reply — no tag.
**No STT fallback** — only `char:*` switches persona (same as `mv:*`)."""


def sleep_tag_prompt(*, example_tone: str = "kira") -> str:
    if example_tone == "lili":
        examples = (
            '✅ *"Ngủ ngon nha, mai chơi tiếp sleep"*',
        )
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


def memory_tags_prompt(*, compact: bool = False) -> str:
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
