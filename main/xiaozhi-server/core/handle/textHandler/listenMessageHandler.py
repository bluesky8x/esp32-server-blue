import time
import uuid
import asyncio
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.utils.dialogue import Message
from core.providers.asr.dto.dto import InterfaceType
from core.handle.receiveAudioHandle import startToChat
from core.handle.reportHandle import enqueue_asr_report
from core.handle.sendAudioHandle import send_stt_message
from core.handle.abortHandle import handleAbortMessage
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.characters.character_switch import handle_character_wake, match_character_wake
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType


TAG = __name__

# Ignore duplicate listen/start from firmware resync (TTS stop, motor, playback drain).
LISTEN_START_DEBOUNCE_SEC = 2.0


class ListenTextMessageHandler(TextMessageHandler):
    """Listen消息处理器"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.LISTEN

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        if "mode" in msg_json:
            conn.client_listen_mode = msg_json["mode"]
            conn.logger.bind(tag=TAG).debug(
                f"客户端拾音模式：{conn.client_listen_mode}"
            )
        if msg_json["state"] == "start":
            now = time.time()
            last = getattr(conn, "_last_listen_start_at", 0.0)
            debounced = last and (now - last) < LISTEN_START_DEBOUNCE_SEC

            # Duplicate listen/start while startup greeting plays — ignore entirely.
            if getattr(conn, "_startup_greeting_in_progress", False) and conn.client_is_speaking:
                conn.logger.bind(tag=TAG).debug(
                    "listen start ignored — startup greeting in progress"
                )
                return

            if debounced:
                conn.logger.bind(tag=TAG).debug(
                    f"listen start debounced ({now - last:.2f}s since last)"
                )

            conn._last_listen_start_at = now

            # Touch reset: abort stuck wx/TTS and sync device speaking → listening
            if getattr(conn, "_wx_followup_pending", False):
                conn._schedule_listening_recovery(
                    reason="listen start (wx abort)", abort_tts=True
                )
            elif debounced:
                conn.clearSpeakStatus()
                conn.reset_audio_states()
            elif conn.client_is_speaking:
                conn._schedule_listening_recovery(
                    reason="listen start (speaking reset)", abort_tts=False
                )
            else:
                conn.clearSpeakStatus()
                conn.reset_audio_states()

            if debounced:
                return

            from core.utils.wake_greeting import maybe_speak_startup_greeting

            asyncio.create_task(maybe_speak_startup_greeting(conn))
        elif msg_json["state"] == "stop":
            # 收到stop但asr未初始化，跳过处理
            if conn.asr is None:
                return

            conn.client_voice_stop = True
            if conn.asr.interface_type == InterfaceType.STREAM:
                # 流式模式下，发送结束请求
                asyncio.create_task(conn.asr._send_stop_request())
            else:
                # 非流式模式：直接触发ASR识别
                if len(conn.asr_audio) > 0:
                    asr_audio_task = conn.asr_audio.copy()
                    conn.reset_audio_states()

                    if len(asr_audio_task) > 0:
                        await conn.asr.handle_voice_stop(conn, asr_audio_task)
        elif msg_json["state"] == "detect":
            conn.client_have_voice = False
            conn.reset_audio_states()
            if "text" in msg_json:
                conn.last_activity_time = time.time() * 1000
                original_text = msg_json["text"]  # 保留原始文本

                # 检查是否是设备呼叫指令 [device_call]
                if original_text.startswith("[device_call]"):
                    # 提取 tag 后的文本
                    call_text = original_text[len("[device_call]"):].strip()
                    conn.logger.bind(tag=TAG).info(f"收到设备呼叫指令: {call_text}")

                    # 标记为来电接听模式
                    conn.incoming_call = True

                    # 准备开始新会话
                    conn.sentence_id = uuid.uuid4().hex

                    await send_stt_message(conn, call_text)

                    # 等待tts初始化，最多等待3秒
                    start_time = time.time()
                    while time.time() - start_time < 3:
                        if conn.tts:
                            break
                        await asyncio.sleep(0.1)

                    if conn.tts:
                        conn.tts.store_tts_text(conn.sentence_id, call_text)
                        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=SentenceType.FIRST, content_type=ContentType.ACTION))
                        conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=call_text)
                        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=SentenceType.LAST, content_type=ContentType.ACTION))

                    # 添加到对话历史，让模型理解上下文
                    conn.dialogue.put(Message(role="assistant", content=call_text))
                    return

                # 角色唤醒词：先切换角色，避免后续 abort 打断 TTS
                char_id, wake_only, _remainder = match_character_wake(
                    original_text, conn.config
                )
                if char_id:
                    if conn.client_is_speaking:
                        await handleAbortMessage(conn)
                    conn.client_abort = False
                    conn.sentence_id = uuid.uuid4().hex
                    chat_text = await handle_character_wake(conn, original_text)
                    if chat_text is None:
                        return
                    original_text = chat_text

                conn.just_woken_up = True
                enqueue_asr_report(conn, original_text, [])
                await startToChat(conn, original_text)