import os
import sys
import copy
import json
import re
import uuid
import time
import queue
import asyncio
import threading
import traceback
import subprocess
import websockets
import opuslib_next
import numpy as np

from core.utils.util import (
    extract_json_from_string,
    check_vad_update,
    check_asr_update,
    filter_sensitive_info,
)
from typing import Dict, Any
from collections import deque
from core.utils.modules_initialize import (
    initialize_modules,
    initialize_tts,
    initialize_asr,
)
from core.handle.reportHandle import report, enqueue_tool_report
from core.providers.tts.default import DefaultTTS
from concurrent.futures import ThreadPoolExecutor
from core.utils.dialogue import Message, Dialogue
from core.providers.asr.dto.dto import InterfaceType
from core.handle.textHandle import handleTextMessage
from plugins_func.loadplugins import auto_import_modules
from plugins_func.register import Action, ActionResponse
from core.auth import AuthenticationError
from config.config_loader import get_private_config_from_api
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType
from config.logger import setup_logging, build_module_string, create_connection_logger
from config.manage_api_client import DeviceNotFoundException, DeviceBindException, generate_and_save_chat_title
from core.utils.prompt_manager import PromptManager
from core.utils.voiceprint_provider import VoiceprintProvider
from core.utils.util import get_system_error_response
from core.utils import textUtils


TAG = __name__

# Per-speaker dialogue bucket used when voiceprint returns the unknown marker.
_UNKNOWN_DIALOGUE_BUCKET = "__unknown__"

auto_import_modules("plugins_func.functions")


class TTSException(RuntimeError):
    pass

# direct_answer 虚拟工具定义
# 不是真实工具，是路由机制：将"调不调工具"的二选一变为"调哪个"的多选，防止小模型误触发真实工具
DIRECT_ANSWER_TOOL = {
    "type": "function",
    "function": {
        "name": "direct_answer",
        "description": "当用户的请求不匹配其他任何工具时，可用此选项直接回复。将回复内容写在response参数里。",
        "parameters": {
            "type": "object",
            "properties": {
                "response": {
                    "type": "string",
                    "description": "你回复用户的完整内容",
                },
            },
            "required": ["response"],
        },
    },
}


class ConnectionHandler:
    def __init__(
            self,
            config: Dict[str, Any],
            _vad,
            _asr,
            _llm,
            _memory,
            _intent,
            server=None,
    ):
        self.common_config = config
        self.config = copy.deepcopy(config)
        self.session_id = str(uuid.uuid4())
        self.logger = setup_logging()
        self.server = server  # 保存server实例的引用

        self.need_bind = False  # 是否需要绑定设备
        self.bind_completed_event = asyncio.Event()
        self.bind_code = None  # 绑定设备的验证码
        self.last_bind_prompt_time = 0  # 上次播放绑定提示的时间戳(秒)
        self.bind_prompt_interval = 60  # 绑定提示播放间隔(秒)

        self.read_config_from_api = self.config.get("read_config_from_api", False)

        self.websocket: websockets.ServerConnection | None = None
        self.headers = None
        self.device_id = None
        self.client_ip = None
        self.prompt = None
        self.welcome_msg = None
        self.max_output_size = 0
        self.chat_history_conf = 0
        self.audio_format = "opus"
        self.sample_rate = 24000  # 默认采样率，从客户端 hello 消息中动态更新

        # 客户端状态相关
        self.client_abort = False
        self.client_is_speaking = False
        self._chat_active = False
        self._wx_followup_pending = False
        self._wx_followup_sentence_id = None
        self._wx_followup_watchdog_task = None
        self.client_listen_mode = "auto"
        self.client_aec = False  # 是否启用了服务端AEC

        # 线程任务相关
        self.loop = None  # 在 handle_connection 中获取运行中的事件循环
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=5)

        # 添加上报线程池
        self.report_queue = queue.Queue()
        self.report_thread = None
        # 未来可以通过修改此处，调节asr的上报和tts的上报，目前默认都开启
        self.report_asr_enable = self.read_config_from_api
        self.report_tts_enable = self.read_config_from_api

        # 依赖的组件
        self.vad = None
        self.asr = None
        self.tts = None
        self._asr = _asr
        self._vad = _vad
        self.llm = _llm
        self.memory = _memory
        self.intent = _intent

        # 为每个连接单独管理声纹识别
        self.voiceprint_provider = None
        self.voice_user_store = None
        self._last_voice_wav = None
        self._voice_enroll_state = None

        # vad相关变量
        self.client_audio_buffer = bytearray()
        self.client_have_voice = False
        self.client_voice_window = deque(maxlen=5)
        self.first_activity_time = 0.0  # 记录首次活动的时间（毫秒）
        self.last_activity_time = 0.0  # 统一的活动时间戳（毫秒）
        self.vad_last_voice_time = 0.0
        self.vad_speech_start_time = 0.0
        self.client_voice_stop = False
        self.last_is_voice = False

        # asr相关变量
        # 因为实际部署时可能会用到公共的本地ASR，不能把变量暴露给公共ASR
        # 所以涉及到ASR的变量，需要在这里定义，属于connection的私有变量
        self.asr_audio = []  # 存储PCM帧列表，供VAD和ASR共享
        # Robot sends uplink immediately after connect; buffer until VAD/ASR init finishes.
        self._early_audio_buffer: list[bytes] = []
        self._early_audio_buffer_max = 150  # ~3s at 60ms frames
        self._early_audio_logged = False
        self.asr_audio_queue = queue.Queue()
        self.current_speaker = None  # 存储当前说话人
        self.introduced_speakers = set()  # 已"首次引入"的说话人，控制只在首轮带名字
        self.system_introduced_speakers = set()  # 已在 system 注入过身份的说话人，控制 system 身份只首轮出现

        # llm相关变量
        self.dialogue = Dialogue()
        # Multi-user conversation context: each registered speaker keeps their
        # own last discussion so topics don't mix between users.
        self._user_dialogues: dict = {}
        self._active_dialogue_user: str | None = None
        self._user_context_last_saved: dict = {}  # bucket -> last file save (epoch s)

        # tts相关变量
        self.sentence_id = None
        # 处理TTS响应没有文本返回
        self.tts_MessageText = ""

        # iot相关变量
        self.iot_descriptors = {}
        self.func_handler = None
        self._pending_robot_moves: list[tuple[str | None, str, int]] = []
        self._robot_move_sequence_queue: list[tuple[str | None, str, int]] = []
        self._robot_move_in_flight = False
        self._robot_move_pump_scheduled = False
        self._robot_move_cooldown_until = 0.0
        self._robot_move_shutdown = False
        self._robot_move_pump_handle = None

        # Active character (voice-switchable per connection)
        from core.characters.character_registry import is_character_enabled

        default_char = self.config.get("character")
        self.active_character = (
            default_char if is_character_enabled(default_char) else None
        )

        self.cmd_exit = self.config["exit_commands"]

        # 是否在聊天结束后关闭连接
        self.close_after_chat = False
        self.load_function_plugin = False
        self.intent_type = "nointent"

        self.timeout_seconds = (
                int(self.config.get("close_connection_no_voice_time", 120)) + 60
        )  # 在原来第一道关闭的基础上加60秒，进行二道关闭
        self.timeout_task = None

        # {"mcp":true} 表示启用MCP功能
        self.features = None

        # 标记连接是否来自MQTT
        self.conn_from_mqtt_gateway = False

        from core.utils.language_runtime import default_locale

        self.active_locale = default_locale(self.config)
        self.normalize_vietnamese_tts = self.active_locale == "vi"

        # 初始化提示词管理器
        self.prompt_manager = PromptManager(self.config, self.logger)

        # 初始化通话状态
        self.calling = False
        # 标记当前是否为来电接听模式
        self.incoming_call = None

    async def handle_connection(self, ws: websockets.ServerConnection):
        try:
            # 获取运行中的事件循环（必须在异步上下文中）
            self.loop = asyncio.get_running_loop()

            # 获取并验证headers
            self.headers = dict(ws.request.headers)
            real_ip = self.headers.get("x-real-ip") or self.headers.get(
                "x-forwarded-for"
            )
            if real_ip:
                self.client_ip = real_ip.split(",")[0].strip()
            else:
                self.client_ip = ws.remote_address[0]
            self.logger.bind(tag=TAG).info(
                f"{self.client_ip} conn - Headers: {self.headers}"
            )

            self.device_id = self.headers.get("device-id", None)

            # 认证通过,继续处理
            self.websocket = ws

            # 检查是否来自MQTT连接
            request_path = ws.request.path
            self.conn_from_mqtt_gateway = request_path.endswith("?from=mqtt_gateway")
            if self.conn_from_mqtt_gateway:
                self.logger.bind(tag=TAG).info("连接来自:MQTT网关")

            # 初始化活动时间戳
            self.first_activity_time = time.time() * 1000
            self.last_activity_time = time.time() * 1000

            # 启动超时检查任务
            self.timeout_task = asyncio.create_task(self._check_timeout())

            # 启动AEC缓存清理任务
            self._aec_cache_cleanup_task = asyncio.create_task(self._check_aec_cache_expiry())

            self.welcome_msg = self.config["xiaozhi"]
            self.welcome_msg["session_id"] = self.session_id

            # 从配置中读取采样率
            self.sample_rate = self.welcome_msg["audio_params"]["sample_rate"]
            self.logger.bind(tag=TAG).info(f"配置输出音频采样率为: {self.sample_rate}")

            # 在后台初始化配置和组件（完全不阻塞主循环）
            asyncio.create_task(self._background_initialize())

            try:
                async for message in self.websocket:
                    await self._route_message(message)
            except websockets.exceptions.ConnectionClosed:
                self.logger.bind(tag=TAG).info("客户端断开连接")

        except AuthenticationError as e:
            self.logger.bind(tag=TAG).error(f"Authentication failed: {str(e)}")
            return
        except Exception as e:
            stack_trace = traceback.format_exc()
            self.logger.bind(tag=TAG).error(f"Connection error: {str(e)}-{stack_trace}")
            return
        finally:
            try:
                await self._save_and_close(ws)
            except Exception as final_error:
                self.logger.bind(tag=TAG).error(f"最终清理时出错: {final_error}")
                # 确保即使保存记忆失败，也要关闭连接
                try:
                    await self.close(ws)
                except Exception as close_error:
                    self.logger.bind(tag=TAG).error(
                        f"强制关闭连接时出错: {close_error}"
                    )

    async def _save_and_close(self, ws):
        """保存记忆并关闭连接"""
        try:
            # 守护线程1：独立生成标题（不依赖记忆模型；需 manager-api）
            if self.session_id:
                from config.manage_api_client import ManageApiClient

                if ManageApiClient._instance:
                    def generate_title_task():
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            loop.run_until_complete(
                                generate_and_save_chat_title(self.session_id)
                            )
                        except Exception as e:
                            self.logger.bind(tag=TAG).error(f"生成标题失败: {e}")
                        finally:
                            try:
                                loop.close()
                            except Exception:
                                pass

                    threading.Thread(target=generate_title_task, daemon=True).start()

            # 守护线程2：走老流程记忆保存（仅记忆，不含标题）
            if self.memory:
                # 使用线程池异步保存记忆
                def save_memory_task():
                    try:
                        # 创建新事件循环（避免与主循环冲突）
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(
                            self.memory.save_memory(
                                self.dialogue.dialogue, self.session_id
                            )
                        )
                    except Exception as e:
                        self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
                    finally:
                        try:
                            loop.close()
                        except Exception:
                            pass

                # 启动线程保存记忆，不等待完成
                threading.Thread(target=save_memory_task, daemon=True).start()
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"保存记忆失败: {e}")
        finally:
            # 立即关闭连接，不等待记忆保存完成
            try:
                await self.close(ws)
            except Exception as close_error:
                self.logger.bind(tag=TAG).error(
                    f"保存记忆后关闭连接失败: {close_error}"
                )

    async def _discard_message_with_bind_prompt(self):
        """丢弃消息并检查是否需要播放绑定提示"""
        current_time = time.time()
        # 检查是否需要播放绑定提示
        if current_time - self.last_bind_prompt_time >= self.bind_prompt_interval:
            self.last_bind_prompt_time = current_time
            # 复用现有的绑定提示逻辑
            from core.handle.receiveAudioHandle import check_bind_device

            asyncio.create_task(check_bind_device(self))

    async def _route_message(self, message):
        """消息路由"""
        # 检查是否已经获取到真实的绑定状态
        if not self.bind_completed_event.is_set():
            # 还没有获取到真实状态，等待直到获取到真实状态或超时
            try:
                await asyncio.wait_for(self.bind_completed_event.wait(), timeout=1)
            except asyncio.TimeoutError:
                # 超时仍未获取到真实状态，丢弃消息
                await self._discard_message_with_bind_prompt()
                return

        # 已经获取到真实状态，检查是否需要绑定
        if self.need_bind:
            # 需要绑定，丢弃消息
            await self._discard_message_with_bind_prompt()
            return

        # 不需要绑定，继续处理消息

        if isinstance(message, str):
            await handleTextMessage(self, message)
        elif isinstance(message, bytes):
            # 处理来自MQTT网关的音频包
            if self.conn_from_mqtt_gateway and len(message) >= 16:
                if self.vad is None or self.asr is None:
                    return
                handled = await self._process_mqtt_audio_message(message)
                if handled:
                    return

            pcm_frame = self._decode_opus_packet(message)
            if not pcm_frame:
                return

            # AutoStop firmware only uplinks in listening — if speaking flag is still
            # set (slow TTS stop, greeting, or watchdog desync), accept the audio.
            if (
                self.client_is_speaking
                and self.client_listen_mode != "manual"
                and not getattr(self, "_startup_greeting_in_progress", False)
            ):
                self.logger.bind(tag=TAG).info(
                    "Device uplink while speaking flag set — clearing stale speaking state"
                )
                self.clearSpeakStatus()

            if self.vad is None or self.asr is None:
                self._buffer_early_audio(pcm_frame)
                return

            self.asr_audio_queue.put(pcm_frame)

    async def _process_mqtt_audio_message(self, message):
        """
        处理来自MQTT网关的音频消息，解析16字节头部并提取音频数据，在入队前进行AEC处理

        Args:
            message: 包含头部的音频消息

        Returns:
            bool: 是否成功处理了消息
        """
        try:
            # 解析timestamp
            timestamp = int.from_bytes(message[8:12], "big")

            audio_data = message[16:]
            # 入口直接解码PCM
            pcm_frame = self._decode_opus_packet(audio_data)
            if not pcm_frame:
                return True

            # AEC处理：如果timestamp>0且启用了AEC
            if timestamp > 0 and self.client_aec:
                pcm_frame = self._apply_aec(timestamp, pcm_frame)

            self.asr_audio_queue.put(pcm_frame)
            return True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"解析WebSocket音频包失败: {e}")

        # 处理失败，返回False表示需要继续处理
        return False

    def _apply_aec(self, timestamp: int, pcm_frame: bytes) -> bytes:
        """应用AEC处理 - 综合算法：互相关延迟估计 + Wiener滤波 + 频谱减法"""
        try:
            if not pcm_frame or len(pcm_frame) == 0:
                return pcm_frame

            if not hasattr(self, "aec_audio_cache") or not self.aec_audio_cache:
                return pcm_frame

            mic_audio = np.frombuffer(pcm_frame, dtype=np.int16).astype(np.float32)
            mic_rms = np.sqrt(np.mean(mic_audio ** 2))

            if mic_rms < 100:
                return pcm_frame

            sorted_timestamps = sorted(self.aec_audio_cache.keys())
            if len(sorted_timestamps) < 2:
                return pcm_frame

            # ========== 匹配参考帧（对数功率谱匹配） ==========
            n = len(mic_audio)

            # 找最接近的timestamp作为起点
            closest_idx = min(range(len(sorted_timestamps)), key=lambda i: abs(sorted_timestamps[i] - timestamp))

            # 预计算 mic_audio 的对数功率谱（循环内共用，避免重复FFT）
            mic_window = np.hanning(n)
            mic_fft = np.fft.rfft(mic_audio * mic_window)
            mic_psd = np.abs(mic_fft) ** 2
            mic_log_psd = 10 * np.log10(mic_psd + 1e-8)
            mic_P_xx = np.dot(mic_log_psd, mic_log_psd)

            # 用对数功率谱匹配找最佳帧：前后各找2帧
            best_corr = -1
            best_ref_idx = closest_idx
            best_ref_rms = 0.0

            for offset in range(-2, 3):  # T-2, T-1, T, T+1, T+2
                test_idx = closest_idx + offset
                if test_idx < 0 or test_idx >= len(sorted_timestamps):
                    continue
                test_ts = sorted_timestamps[test_idx]
                test_ref = np.frombuffer(self.aec_audio_cache[test_ts], dtype=np.int16).astype(np.float32)
                test_ref_rms = np.sqrt(np.mean(test_ref ** 2))
                if test_ref_rms < 50:
                    continue

                # 对数功率谱相关性
                test_window = np.hanning(len(test_ref))
                test_fft = np.fft.rfft(test_ref * test_window)
                test_psd = np.abs(test_fft) ** 2
                test_log_psd = 10 * np.log10(test_psd + 1e-8)
                P_xy = np.dot(mic_log_psd, test_log_psd)
                P_yy = np.dot(test_log_psd, test_log_psd)
                corr = abs(P_xy) / (np.sqrt(mic_P_xx) * np.sqrt(P_yy) + 1e-8)

                if corr > best_corr:
                    best_corr = corr
                    best_ref_idx = test_idx
                    best_ref_rms = test_ref_rms

            best_ts = sorted_timestamps[best_ref_idx]
            best_ref = np.frombuffer(self.aec_audio_cache[best_ts], dtype=np.int16).astype(np.float32)
            ref_rms = best_ref_rms

            if ref_rms < 50:
                return pcm_frame

            # 对齐参考信号（直接截取相同长度）
            aligned_ref = best_ref[:n]
            if len(aligned_ref) < n:
                aligned_ref = np.pad(aligned_ref, (0, n - len(aligned_ref)))

            # ========== 频域 AEC 处理（谱减法） ==========
            # 时域信号经过声学路径后相位失真，导致时域相关性低且P_xy正负不定
            # 频域幅度谱不受相位影响，对数功率谱相关性稳定在0.97+
            # 公式：result_mag = max(|mic_fft| - |ref_fft| * scale * coef, 0)

            mic_mag = np.abs(mic_fft)
            mic_phase = np.angle(mic_fft)
            ref_fft = np.fft.rfft(aligned_ref * np.hanning(n))
            ref_mag = np.abs(ref_fft)

            # 频域计算回声比例 scale
            scale = np.sum(mic_mag * ref_mag) / (np.dot(ref_mag, ref_mag) + 1e-8)

            # 自适应系数：根据scale和coh动态调整
            # scale大（回声强）-> coef大；coh高（匹配准）-> coef大
            raw_coef = 1.0 + scale * 3 + (best_corr - 0.97) * 30
            coef = max(0.5, min(3.0, raw_coef))

            # 谱减法（过减 + 半波整流）
            echo_mag = ref_mag * scale * coef
            result_mag = np.maximum(mic_mag - echo_mag * 1.5, mic_mag * 0.1)

            # 保留相位重建信号
            result_fft = result_mag * np.exp(1j * mic_phase)
            output = np.fft.irfft(result_fft, n)

            # 高置信度是纯回声时，再压一下确保VAD检测不到
            if best_corr >= 0.97 and ref_rms > 500:
                output = output * 0.3

            # 后处理：限幅
            output = np.clip(output, -32768, 32767)

            # 转换为bytes
            result = output.astype(np.int16).tobytes()

            return result

        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"[AEC] 处理失败: {e}")
            return pcm_frame

    def _decode_opus_packet(self, opus_packet: bytes) -> bytes:
        """
        解码Opus数据包为PCM数据

        Args:
            opus_packet: Opus编码的音频数据

        Returns:
            bytes: 解码后的PCM数据，失败返回None
        """
        try:
            if not opus_packet or len(opus_packet) == 0:
                return None

            self._init_connection_state(self)
            pcm_frame = self._connection_opus_decoder.decode(opus_packet, 960)
            return pcm_frame
        except Exception as e:
            self.logger.bind(tag=TAG).debug(f"Opus解码失败: {e}")
            return None

    def _init_connection_state(self, conn):
        """为连接初始化独立的Opus解码器"""
        if not hasattr(conn, "_connection_opus_decoder"):
            conn._connection_opus_decoder = opuslib_next.Decoder(16000, 1)

    async def handle_restart(self, message):
        """处理服务器重启请求"""
        try:

            self.logger.bind(tag=TAG).info("收到服务器重启指令，准备执行...")

            # 发送确认响应
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "success",
                        "message": "服务器重启中...",
                        "content": {"action": "restart"},
                    }
                )
            )

            # 异步执行重启操作
            def restart_server():
                """实际执行重启的方法"""
                time.sleep(1)
                self.logger.bind(tag=TAG).info("执行服务器重启...")
                subprocess.Popen(
                    [sys.executable, "app.py"],
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                    start_new_session=True,
                )
                os._exit(0)

            # 使用线程执行重启避免阻塞事件循环
            threading.Thread(target=restart_server, daemon=True).start()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"重启失败: {str(e)}")
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": f"Restart failed: {str(e)}",
                        "content": {"action": "restart"},
                    }
                )
            )

    def _buffer_early_audio(self, pcm_frame: bytes) -> None:
        self._early_audio_buffer.append(pcm_frame)
        if len(self._early_audio_buffer) > self._early_audio_buffer_max:
            self._early_audio_buffer.pop(0)
        if not self._early_audio_logged:
            self._early_audio_logged = True
            self.logger.bind(tag=TAG).info(
                "Buffering uplink until VAD/ASR init completes (robot boot)"
            )

    def _flush_early_audio_buffer(self) -> None:
        if not self._early_audio_buffer:
            return
        count = len(self._early_audio_buffer)
        for frame in self._early_audio_buffer:
            self.asr_audio_queue.put(frame)
        self._early_audio_buffer.clear()
        self.logger.bind(tag=TAG).info(
            f"Flushed {count} early audio frame(s) buffered before ASR init"
        )

    def _initialize_components(self):
        try:
            if self.tts is None:
                self.tts = self._initialize_tts()
            if self.tts and self.active_character:
                from core.utils.language_runtime import resolve_tts_voice

                voice = resolve_tts_voice(
                    self.active_character, self.config, self.active_locale
                )
                if voice and hasattr(self.tts, "voice"):
                    self.tts.voice = voice
            # 打开语音合成通道
            asyncio.run_coroutine_threadsafe(
                self.tts.open_audio_channels(self), self.loop
            )
            if self.need_bind:
                self.bind_completed_event.set()
                return
            self.selected_module_str = build_module_string(
                self.config.get("selected_module", {})
            )
            self.logger = create_connection_logger(self.selected_module_str)

            """初始化组件"""
            if self.config.get("prompt") is not None:
                user_prompt = self.config["prompt"]
                # 使用快速提示词进行初始化
                prompt = self.prompt_manager.get_quick_prompt(user_prompt)
                self.change_system_prompt(prompt)
                self.logger.bind(tag=TAG).info(
                    f"快速初始化组件: prompt成功 {prompt[:50]}..."
                )

            """初始化本地组件"""
            if self.vad is None:
                self.vad = self._vad
            if self.asr is None:
                self.asr = self._initialize_asr()

            from core.utils.language_runtime import apply_locale_to_connection

            apply_locale_to_connection(self, self.active_locale, reason="init")

            # 初始化声纹识别
            self._initialize_voiceprint()
            # 打开语音识别通道
            asyncio.run_coroutine_threadsafe(
                self.asr.open_audio_channels(self), self.loop
            )
            self._flush_early_audio_buffer()

            """加载记忆"""
            self._initialize_memory()
            """加载意图识别"""
            self._initialize_intent()
            """设备 MCP / mv 标签需要 func_handler，与 intent 模块独立"""
            self._ensure_func_handler()
            """初始化上报线程"""
            self._init_report_threads()
            """更新系统提示词"""
            self._init_prompt_enhancement()
            """注入工具调用few-shot示例（仅function_call模式）"""
            self._inject_tool_call_fewshot()
            """注入 move/dance few-shot（plain text — mọi intent mode）"""
            self._inject_move_fewshots()

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"实例化组件失败: {e}")

    def _init_prompt_enhancement(self):
        from core.characters.character_registry import get_operational_prompt

        # 更新上下文信息
        self.prompt_manager.update_context_info(self, self.client_ip)
        enhanced_prompt = self.prompt_manager.build_enhanced_prompt(
            get_operational_prompt(
                self.active_character or self.config.get("character") or "kira",
                getattr(self, "active_locale", "vi"),
                enable_voiceprint_resample=self._voice_enroll_enabled(),
            ),
            self.device_id,
            self.client_ip,
            active_character=self.active_character,
            emoji_enabled=(self.features or {}).get("emoji", True),
            locale=getattr(self, "active_locale", "vi"),
        )
        if enhanced_prompt:
            self.change_system_prompt(enhanced_prompt)
            self.logger.bind(tag=TAG).debug("系统提示词已增强更新")

    def _robot_move_available_tools(self) -> set[str]:
        names: set[str] = set()
        if getattr(self, "func_handler", None):
            try:
                names.update(
                    t.get("function", {}).get("name")
                    for t in self.func_handler.get_functions()
                    if t.get("function", {}).get("name")
                )
            except Exception:
                pass
        mcp = getattr(self, "mcp_client", None)
        if mcp is not None and getattr(mcp, "tools", None):
            names.update(mcp.tools.keys())
        return names

    def _ensure_func_handler(self):
        """Ensure UnifiedToolHandler exists (device MCP, mv tags, plugins)."""
        if getattr(self, "func_handler", None) is not None:
            return self.func_handler
        from core.providers.tools.unified_tool_handler import UnifiedToolHandler

        self.logger.bind(tag=TAG).info("[tool] creating func_handler")
        self.func_handler = UnifiedToolHandler(self)
        if hasattr(self, "loop") and self.loop:
            asyncio.run_coroutine_threadsafe(self.func_handler._initialize(), self.loop)
        return self.func_handler

    def _robot_move_step_delay(self) -> float:
        return float(self.config.get("robot_move_step_delay_seconds", 5))

    def _robot_move_max_sequence(self) -> int:
        return int(self.config.get("robot_move_max_sequence", 5))

    def _robot_move_default_duration(self) -> int:
        return int(self.config.get("robot_move_default_duration_seconds", 5))

    def _robot_move_max_duration(self) -> int:
        return int(self.config.get("robot_move_max_duration_seconds", 30))

    def _robot_move_allow_inference(self) -> bool:
        block = self.config.get("robot_move")
        if isinstance(block, dict):
            return bool(block.get("allow_inference", False))
        return bool(self.config.get("robot_move_allow_inference", False))

    def _shutdown_robot_moves(self) -> None:
        """Cancel pending motor timers and drop queued moves on disconnect/shutdown."""
        self._robot_move_shutdown = True
        handle = getattr(self, "_robot_move_pump_handle", None)
        if handle is not None:
            handle.cancel()
        self._robot_move_pump_handle = None
        self._robot_move_sequence_queue.clear()
        self._pending_robot_moves.clear()
        self._robot_move_pump_scheduled = False
        self._robot_move_in_flight = False

    def emergency_stop_robot_moves(
        self, *, label: str = "", dispatch_stop: bool = True
    ) -> int:
        """Failsafe: drop all queued/pending mv steps and send motor stop now."""
        queued = len(getattr(self, "_robot_move_sequence_queue", None) or [])
        pending = len(getattr(self, "_pending_robot_moves", None) or [])
        cleared = queued + pending

        handle = getattr(self, "_robot_move_pump_handle", None)
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass
        self._robot_move_pump_handle = None
        self._robot_move_sequence_queue.clear()
        self._pending_robot_moves.clear()
        self._robot_move_pump_scheduled = False
        self._robot_move_cooldown_until = 0.0

        if dispatch_stop:
            self._dispatch_motor_stop_immediate(label=label)

        if cleared or dispatch_stop:
            self.logger.bind(tag=TAG).info(
                f"[mv] emergency stop — cleared {cleared} queued/pending "
                f"(in_flight={getattr(self, '_robot_move_in_flight', False)}, "
                f"from={label or 'unknown'})"
            )
        return cleared

    def _schedule_post_tts_action(self, label: str, action) -> None:
        """Queue device side-effect until TTS stop (motor/ToF/volume must not run over TTS)."""
        if not hasattr(self, "_post_tts_action_queue"):
            self._post_tts_action_queue = []
        if any(existing_label == label for existing_label, _ in self._post_tts_action_queue):
            self.logger.bind(tag=TAG).debug(f"[post_tts] skip duplicate {label}")
            return
        self._post_tts_action_queue.append((label, action))
        self.logger.bind(tag=TAG).debug(f"[post_tts] queued {label}")

    def flush_post_tts_actions(self) -> None:
        queue = getattr(self, "_post_tts_action_queue", None) or []
        self._post_tts_action_queue = []
        if not queue:
            return
        # Collapse duplicate labels (stream + prepare_llm often queue mv_steps twice).
        seen_labels: set[str] = set()
        unique_queue = []
        for label, action in queue:
            if label in seen_labels:
                continue
            seen_labels.add(label)
            unique_queue.append((label, action))
        self.logger.bind(tag=TAG).info(
            f"[post_tts] flushing {len(unique_queue)} deferred action(s): "
            f"{[label for label, _ in unique_queue]}"
        )
        for label, action in unique_queue:
            try:
                action()
            except Exception as exc:
                self.logger.bind(tag=TAG).error(f"[post_tts] failed {label}: {exc}")

    def _dispatch_motor_stop_immediate(self, *, label: str = "") -> None:
        """Send self.motor.stop immediately — bypasses queue and dedupe."""
        from core.utils.robot_move_codec import RobotMoveStep, build_mcp_call

        if not getattr(self, "func_handler", None) or not getattr(self, "loop", None):
            self.logger.bind(tag=TAG).warning(
                "[mv] emergency stop — func_handler/loop missing"
            )
            return

        step = RobotMoveStep(code="s", duration_sec=0)
        tool_name, tool_args = build_mcp_call(
            step, self._robot_move_available_tools()
        )
        if not tool_name:
            self.logger.bind(tag=TAG).warning(
                "[mv] emergency stop — no motor stop MCP tool"
            )
            return

        args_json = json.dumps(tool_args, ensure_ascii=False)
        self.logger.bind(tag=TAG).info(
            f"[mv] emergency stop → {tool_name} args={args_json} "
            f"(from={label or 'unknown'})"
        )

        def _on_stop_done(fut):
            try:
                fut.result()
            except Exception as exc:
                self.logger.bind(tag=TAG).error(
                    f"[mv] emergency stop MCP failed: {exc}"
                )

        future = asyncio.run_coroutine_threadsafe(
            self.func_handler.handle_llm_function_call(
                self, {"name": tool_name, "arguments": args_json}
            ),
            self.loop,
        )
        future.add_done_callback(_on_stop_done)

    def _dispatch_set_volume(self, volume: int, *, label: str = "") -> None:
        """Send self.audio_speaker.set_volume to the device (works with nointent + vol: tags)."""
        from core.utils.util import sanitize_tool_name
        from core.utils.volume_tag_codec import clamp_volume

        volume = clamp_volume(volume)
        last = getattr(self, "_last_dispatched_volume", None)
        if last == volume:
            return

        if not getattr(self, "func_handler", None) or not getattr(self, "loop", None):
            self.logger.bind(tag=TAG).warning("[vol] skip — func_handler/loop missing")
            return

        candidates = (
            "self.audio_speaker.set_volume",
            "self_audio_speaker_set_volume",
        )
        available = self._robot_move_available_tools()
        tool_name = None
        for name in candidates:
            if name in available:
                tool_name = name
                break
            sanitized = sanitize_tool_name(name)
            if sanitized in available:
                tool_name = sanitized
                break

        if not tool_name:
            self.logger.bind(tag=TAG).warning(
                "[vol] skip — set_volume tool not available "
                f"(available={len(available)})"
            )
            return

        self._last_dispatched_volume = volume
        args_json = json.dumps({"volume": volume}, ensure_ascii=False)
        self.logger.bind(tag=TAG).info(
            f"[vol] dispatch vol:{volume} → {tool_name} args={args_json} "
            f"(from={label or 'unknown'})"
        )

        def _on_vol_done(fut, vol=volume, tool=tool_name):
            try:
                fut.result()
                self.logger.bind(tag=TAG).info(
                    f"[vol] done vol:{vol} → {tool}"
                )
            except Exception as exc:
                self.logger.bind(tag=TAG).error(
                    f"[vol] failed vol:{vol} → {tool}: {exc}"
                )

        future = asyncio.run_coroutine_threadsafe(
            self.func_handler.handle_llm_function_call(
                self, {"name": tool_name, "arguments": args_json}
            ),
            self.loop,
        )
        future.add_done_callback(_on_vol_done)

    def _schedule_post_tts_tof_calibrate(self, distance_mm: int, *, label: str = "") -> None:
        """Defer ToF calibrate until after TTS + user positioning time."""
        from core.handle.sendAudioHandle import POST_TTS_TOF_CAL_DELAY_SEC

        dedupe_label = "tof:cal:auto" if distance_mm == 0 else f"tof:cal:{distance_mm}"

        def _start() -> None:
            loop = getattr(self, "loop", None)
            if not loop:
                self._dispatch_tof_calibrate(distance_mm, label=label)
                return

            async def _delayed() -> None:
                await asyncio.sleep(POST_TTS_TOF_CAL_DELAY_SEC)
                self._dispatch_tof_calibrate(distance_mm, label=label)

            asyncio.run_coroutine_threadsafe(_delayed(), loop)

        self._schedule_post_tts_action(dedupe_label, _start)

    def _dispatch_tof_calibrate(self, distance_mm: int, *, label: str = "") -> None:
        """Send self.tof.calibrate to the device (works with nointent + tof:cal tags)."""
        from core.utils.tof_tag_codec import clamp_calibration_distance
        from core.utils.util import sanitize_tool_name

        distance_mm = clamp_calibration_distance(distance_mm)
        dedupe_key = ("tof_cal", "auto" if distance_mm == 0 else distance_mm)
        if getattr(self, "_executed_tof_calibrate", None) == dedupe_key:
            return

        if not getattr(self, "func_handler", None) or not getattr(self, "loop", None):
            self.logger.bind(tag=TAG).warning("[tof] skip — func_handler/loop missing")
            return

        candidates = (
            "self.tof.calibrate",
            "self_tof_calibrate",
        )
        available = self._robot_move_available_tools()
        tool_name = None
        for name in candidates:
            if name in available:
                tool_name = name
                break
            sanitized = sanitize_tool_name(name)
            if sanitized in available:
                tool_name = sanitized
                break

        if not tool_name:
            self.logger.bind(tag=TAG).warning(
                "[tof] skip — calibrate tool not available "
                f"(available={len(available)})"
            )
            return

        self._executed_tof_calibrate = dedupe_key
        args_json = json.dumps({"distance_mm": distance_mm}, ensure_ascii=False)
        mode = "auto" if distance_mm == 0 else str(distance_mm)
        self.logger.bind(tag=TAG).info(
            f"[tof] dispatch tof:cal ({mode}) → {tool_name} args={args_json} "
            f"(from={label or 'unknown'})"
        )

        def _on_tof_done(fut, dist=distance_mm, tool=tool_name):
            try:
                result = fut.result()
                payload = getattr(result, "result", None) or getattr(result, "response", None)
                self.logger.bind(tag=TAG).info(
                    f"[tof] done tof:cal:{dist} → {tool} result={payload}"
                )
            except Exception as exc:
                self.logger.bind(tag=TAG).error(
                    f"[tof] failed tof:cal:{dist} → {tool}: {exc}"
                )

        future = asyncio.run_coroutine_threadsafe(
            self.func_handler.handle_llm_function_call(
                self, {"name": tool_name, "arguments": args_json}
            ),
            self.loop,
        )
        future.add_done_callback(_on_tof_done)

    def _wx_followup_active(self) -> bool:
        return bool(getattr(self, "_wx_followup_pending", False))

    def _sentence_id_allowed(self, sentence_id: str | None) -> bool:
        if sentence_id is None:
            return True
        if sentence_id == self.sentence_id:
            return True
        wx_id = getattr(self, "_wx_followup_sentence_id", None)
        return bool(wx_id and sentence_id == wx_id)

    def _clear_wx_followup(self, *, reason: str = "") -> None:
        task = getattr(self, "_wx_followup_watchdog_task", None)
        if task and not task.done():
            task.cancel()
        self._wx_followup_watchdog_task = None
        if getattr(self, "_wx_followup_pending", False):
            self._wx_followup_pending = False
            self._wx_followup_sentence_id = None
            if reason:
                self.logger.bind(tag=TAG).info(f"[wx] follow-up cleared ({reason})")

    def _schedule_listening_recovery(
        self, *, reason: str, abort_tts: bool = False
    ) -> None:
        """Reset server + device after wx/TTS stall so touch/mic work again."""
        loop = getattr(self, "loop", None)
        if not loop:
            self._clear_wx_followup(reason=reason)
            self.clearSpeakStatus()
            self.reset_audio_states()
            return

        async def _recover() -> None:
            was_wx = self._wx_followup_active()
            self._clear_wx_followup(reason=reason)

            if abort_tts:
                self.client_abort = True
                self.clear_queues()
                self.client_abort = False

            ws = getattr(self, "websocket", None)
            if ws is not None and not getattr(ws, "closed", False):
                if was_wx or self.client_is_speaking or abort_tts:
                    try:
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "tts",
                                    "state": "stop",
                                    "session_id": self.session_id,
                                }
                            )
                        )
                    except Exception as exc:
                        self.logger.bind(tag=TAG).warning(
                            f"listening recovery tts stop failed: {exc}"
                        )

            self.clearSpeakStatus()
            self.reset_audio_states()
            if hasattr(self, "audio_rate_controller") and self.audio_rate_controller:
                self.audio_rate_controller.stop_sending()
            self.logger.bind(tag=TAG).info(
                f"Listening recovery ({reason}, abort_tts={abort_tts})"
            )

        asyncio.run_coroutine_threadsafe(_recover(), loop)

    def _schedule_wx_followup_watchdog(self, timeout_sec: float = 45.0) -> None:
        """Release mic lock if wx TTS never completes (TTS failure, crash, etc.)."""
        loop = getattr(self, "loop", None)
        if not loop:
            return

        async def _watch() -> None:
            try:
                await asyncio.sleep(timeout_sec)
                if getattr(self, "_wx_followup_pending", False):
                    self.logger.bind(tag=TAG).warning(
                        f"[wx] watchdog timeout ({timeout_sec}s) — releasing mic lock"
                    )
                    self._schedule_listening_recovery(
                        reason=f"watchdog {timeout_sec}s", abort_tts=True
                    )
            except asyncio.CancelledError:
                pass

        old = getattr(self, "_wx_followup_watchdog_task", None)
        if old and not old.done():
            old.cancel()

        def _start() -> None:
            self._wx_followup_watchdog_task = asyncio.create_task(_watch())

        loop.call_soon_threadsafe(_start)

    def _dispatch_weather_lookup(
        self, request, *, label: str = ""
    ) -> None:
        from core.utils.weather_tag_codec import WeatherLookupRequest

        if not isinstance(request, WeatherLookupRequest):
            request = WeatherLookupRequest(
                location="" if request is None else str(request),
                time_key="d0",
                start_offset=0,
                end_offset=0,
                include_current=True,
            )

        if not getattr(self, "loop", None):
            self.logger.bind(tag=TAG).warning("[wx] skip — event loop missing")
            return

        dedupe_key = request.cache_key
        if not hasattr(self, "_executed_weather_lookups"):
            self._executed_weather_lookups = set()
        if dedupe_key in self._executed_weather_lookups:
            self.logger.bind(tag=TAG).debug(
                f"[wx] skip duplicate wx:{dedupe_key} label={label}"
            )
            return
        self._executed_weather_lookups.add(dedupe_key)

        import uuid

        self._wx_followup_pending = True
        self._wx_followup_sentence_id = str(uuid.uuid4().hex)
        self._schedule_wx_followup_watchdog()

        locale = getattr(self, "active_locale", "vi") or "vi"
        self.logger.bind(tag=TAG).info(
            f"[wx] dispatch wx:{request.cache_key} label={label} locale={locale}"
        )

        async def _fetch_and_speak():
            from core.utils.wake_greeting import speak_greeting_txt
            from plugins_func.functions.get_weather import fetch_weather_speech

            import uuid

            spoken = False
            try:
                try:
                    text = await fetch_weather_speech(
                        self,
                        request.location or None,
                        locale=locale,
                        time_request=request,
                    )
                except Exception as exc:
                    self.logger.bind(tag=TAG).error(f"[wx] fetch failed: {exc}")
                    text = None
                if not text:
                    text = (
                        "Mình chưa lấy được thời tiết, thử lại sau nha."
                        if locale != "en"
                        else "I couldn't fetch the weather. Please try again."
                    )
                self.logger.bind(tag=TAG).info(f"[wx] result: {text[:160]}")
                wx_sid = getattr(self, "_wx_followup_sentence_id", None) or str(
                    uuid.uuid4().hex
                )
                self.sentence_id = wx_sid
                self.client_abort = False
                if getattr(self, "tts", None):
                    speak_greeting_txt(self, text)
                    spoken = True
                else:
                    self.logger.bind(tag=TAG).error("[wx] skip speak — TTS missing")
            except Exception as exc:
                self.logger.bind(tag=TAG).error(f"[wx] speak pipeline failed: {exc}")
            finally:
                if not spoken:
                    self._schedule_listening_recovery(
                        reason="speak pipeline failed", abort_tts=False
                    )

        asyncio.run_coroutine_threadsafe(_fetch_and_speak(), self.loop)

    def _dispatch_wx_from_assistant_text(
        self, text: str, *, label: str = "", defer_post_tts: bool = False
    ) -> bool:
        from core.utils.weather_tag_codec import (
            extract_weather_request_from_assistant_text,
        )

        if not text:
            return False
        request = extract_weather_request_from_assistant_text(text)
        if request is None:
            return False
        queue_label = f"wx:{request.cache_key}"
        if defer_post_tts:
            self._schedule_post_tts_action(
                queue_label,
                lambda req=request: self._dispatch_weather_lookup(
                    req, label=label or "assistant"
                ),
            )
            return True
        self._dispatch_weather_lookup(request, label=label or "assistant")
        return True

    def _dispatch_tof_from_assistant_text(
        self, text: str, *, label: str = "", defer_post_tts: bool = False
    ) -> bool:
        from core.utils.tof_tag_codec import extract_tof_calibrate_from_assistant_text

        if not text:
            return False
        distance_mm = extract_tof_calibrate_from_assistant_text(text)
        if distance_mm is None:
            return False
        if defer_post_tts:
            self._schedule_post_tts_action(
                f"tof:{label or 'assistant'}",
                lambda d=distance_mm, l=label: self._schedule_post_tts_tof_calibrate(
                    d, label=l or "assistant"
                ),
            )
            return True
        self._schedule_post_tts_tof_calibrate(distance_mm, label=label or "assistant")
        return True

    def _maybe_dispatch_tof_stt_fallback(self, *, label: str = "", defer_post_tts: bool = True) -> None:
        distance_mm = getattr(self, "_user_requested_tof_calibrate", None)
        if distance_mm is None:
            return
        if getattr(self, "_executed_tof_calibrate", None) == ("tof_cal", distance_mm):
            return
        if defer_post_tts:
            self._schedule_post_tts_action(
                f"tof_stt:{label or 'user_stt_fallback'}",
                lambda d=distance_mm, l=label: self._schedule_post_tts_tof_calibrate(
                    d, label=l or "user_stt_fallback"
                ),
            )
            return
        self._schedule_post_tts_tof_calibrate(distance_mm, label=label or "user_stt_fallback")

    def _dispatch_vol_from_assistant_text(
        self, text: str, *, label: str = "", defer_post_tts: bool = False
    ) -> bool:
        from core.utils.volume_tag_codec import extract_volume_from_assistant_text

        if not text:
            return False
        volume = extract_volume_from_assistant_text(text)
        if volume is None:
            return False
        if defer_post_tts:
            self._schedule_post_tts_action(
                f"vol:{label or 'assistant'}",
                lambda v=volume, l=label: self._dispatch_set_volume(
                    v, label=l or "assistant"
                ),
            )
            return True
        self._dispatch_set_volume(volume, label=label or "assistant")
        return True

    def _maybe_dispatch_volume_stt_fallback(
        self, *, label: str = "", defer_post_tts: bool = True
    ) -> None:
        volume = getattr(self, "_user_requested_volume", None)
        if volume is None:
            return
        # LLM reply already included vol:N — do not apply a conflicting STT guess.
        if getattr(self, "_last_dispatched_volume", None) is not None:
            return
        if defer_post_tts:
            self._schedule_post_tts_action(
                f"vol_stt:{label or 'user_stt_fallback'}",
                lambda v=volume, l=label: self._dispatch_set_volume(
                    v, label=l or "user_stt_fallback"
                ),
            )
            return
        self._dispatch_set_volume(volume, label=label or "user_stt_fallback")

    def _robot_move_cooldown_remaining(self) -> float:
        return max(0.0, getattr(self, "_robot_move_cooldown_until", 0.0) - time.monotonic())

    def _start_robot_move_cooldown(self, step_duration_sec: float | None = None) -> None:
        if step_duration_sec is not None and step_duration_sec > 0:
            delay = float(step_duration_sec)
        else:
            delay = self._robot_move_step_delay()
        self._robot_move_cooldown_until = time.monotonic() + delay
        self.logger.bind(tag=TAG).info(f"[mv] cooldown {delay:.1f}s before next step")

    def _flush_pending_robot_moves(self) -> None:
        """Dispatch mv codes queued while func_handler was still initializing."""
        handler = getattr(self, "func_handler", None)
        if not handler or not handler.finish_init:
            return
        pending = getattr(self, "_pending_robot_moves", None) or []
        if not pending:
            return
        self._pending_robot_moves = []
        self.logger.bind(tag=TAG).info(f"[mv] flushing {len(pending)} pending move(s)")
        for key in pending:
            if key not in self._robot_move_sequence_queue:
                self._robot_move_sequence_queue.append(key)
        self._pump_robot_move_queue()

    def _enqueue_robot_move_steps(
        self, sentence_id: str | None, steps: list
    ) -> list:
        from core.utils.robot_move_codec import RobotMoveStep, format_move_step

        if not hasattr(self, "_executed_robot_moves"):
            self._executed_robot_moves = set()
        added = []
        pending_keys = set(getattr(self, "_pending_robot_moves", None) or [])
        for step in steps:
            if not isinstance(step, RobotMoveStep):
                step = RobotMoveStep(code=str(step), duration_sec=self._robot_move_default_duration())
            key = (sentence_id, step.code, step.duration_sec, step.song)
            if key in self._executed_robot_moves:
                continue
            if key in pending_keys:
                continue
            if key in self._robot_move_sequence_queue:
                continue
            self._robot_move_sequence_queue.append(key)
            added.append(format_move_step(step))
        return added

    def _enqueue_robot_move_codes(
        self, sentence_id: str | None, codes: list[str]
    ) -> list[str]:
        from core.utils.robot_move_codec import (
            RobotMoveStep,
            clamp_duration,
            steps_from_codes,
        )

        default_sec = self._robot_move_default_duration()
        max_sec = self._robot_move_max_duration()
        steps = [
            RobotMoveStep(
                code=c,
                duration_sec=0
                if c == "s"
                else clamp_duration(default_sec, default_sec=default_sec, max_sec=max_sec),
            )
            for c in codes
        ]
        return self._enqueue_robot_move_steps(sentence_id, steps)

    def _pump_robot_move_queue(self) -> None:
        if getattr(self, "_robot_move_shutdown", False):
            return
        if getattr(self, "stop_event", None) and self.stop_event.is_set():
            return
        if getattr(self, "_robot_move_in_flight", False):
            return
        if not self._robot_move_sequence_queue:
            return
        remaining = self._robot_move_cooldown_remaining()
        if remaining > 0:
            self._schedule_robot_move_pump(delay_s=remaining)
            return
        item = self._robot_move_sequence_queue.pop(0)
        if len(item) >= 4:
            sentence_id, code, duration_sec, song = item[0], item[1], item[2], item[3]
        else:
            sentence_id, code, duration_sec = item[0], item[1], item[2]
            song = None
        self._execute_robot_move(sentence_id, code, duration_sec, song=song)

    def _schedule_robot_move_pump(self, delay_s: float | None = None) -> None:
        if getattr(self, "_robot_move_shutdown", False):
            return
        if getattr(self, "stop_event", None) and self.stop_event.is_set():
            return
        if not self._robot_move_sequence_queue:
            return
        if getattr(self, "_robot_move_pump_scheduled", False):
            return
        if delay_s is None:
            delay_s = max(self._robot_move_cooldown_remaining(), self._robot_move_step_delay())
        if delay_s <= 0:
            self._pump_robot_move_queue()
            return
        if not getattr(self, "loop", None):
            self._pump_robot_move_queue()
            return

        self._robot_move_pump_scheduled = True

        def _run() -> None:
            self._robot_move_pump_scheduled = False
            self._robot_move_pump_handle = None
            if getattr(self, "_robot_move_shutdown", False):
                return
            if getattr(self, "stop_event", None) and self.stop_event.is_set():
                return
            try:
                self._pump_robot_move_queue()
            except Exception as exc:
                self.logger.bind(tag=TAG).debug(
                    f"[mv] pump skipped during shutdown: {exc}"
                )

        self.logger.bind(tag=TAG).info(
            f"[mv] next step in {delay_s:.1f}s "
            f"({len(self._robot_move_sequence_queue)} queued)"
        )
        self._robot_move_pump_handle = self.loop.call_later(delay_s, _run)

    def _dispatch_robot_move_steps(
        self, sentence_id: str | None, steps: list, *, defer_post_tts: bool = False
    ) -> None:
        if defer_post_tts:
            if not steps:
                return
            self._schedule_post_tts_action(
                f"mv_steps:{sentence_id}",
                lambda sid=sentence_id, st=list(steps): self._dispatch_robot_move_steps(
                    sid, st, defer_post_tts=False
                ),
            )
            return
        if not steps:
            return

        from core.utils.robot_move_codec import (
            RobotMoveStep,
            clamp_duration,
            format_move_step,
            limit_robot_move_steps,
        )

        default_sec = self._robot_move_default_duration()
        max_sec = self._robot_move_max_duration()
        max_steps = self._robot_move_max_sequence()

        normalized: list[RobotMoveStep] = []
        for step in steps:
            if isinstance(step, RobotMoveStep):
                duration = (
                    0
                    if step.code == "s"
                    else clamp_duration(
                        step.duration_sec,
                        default_sec=default_sec,
                        max_sec=max_sec,
                    )
                )
                normalized.append(RobotMoveStep(code=step.code, duration_sec=duration, song=step.song))
            elif isinstance(step, (list, tuple)) and len(step) >= 2:
                code, dur = step[0], step[1]
                song = step[2] if len(step) >= 3 else None
                duration = (
                    0
                    if code == "s"
                    else clamp_duration(dur, default_sec=default_sec, max_sec=max_sec)
                )
                normalized.append(RobotMoveStep(code=str(code), duration_sec=duration, song=song))
            else:
                code = str(step)
                duration = 0 if code == "s" else default_sec
                normalized.append(RobotMoveStep(code=code, duration_sec=duration))

        if len(normalized) > max_steps:
            self.logger.bind(tag=TAG).warning(
                f"[mv] truncating {len(normalized)} moves to max {max_steps}: "
                f"{[format_move_step(s) for s in normalized]}"
            )
            normalized = limit_robot_move_steps(normalized, max_steps)

        tag_repr = [format_move_step(s) for s in normalized]
        self.logger.bind(tag=TAG).info(
            f"[mv] parsed tags={tag_repr} sentence_id={sentence_id}"
        )

        self._ensure_func_handler()
        handler = self.func_handler
        if not handler.finish_init:
            if not hasattr(self, "_executed_robot_moves"):
                self._executed_robot_moves = set()
            if not hasattr(self, "_pending_robot_moves"):
                self._pending_robot_moves = []
            queued = []
            for step in normalized:
                key = (sentence_id, step.code, step.duration_sec, step.song)
                if key in self._executed_robot_moves:
                    continue
                if key in self._pending_robot_moves:
                    continue
                self._pending_robot_moves.append(key)
                queued.append(format_move_step(step))
            if queued:
                self.logger.bind(tag=TAG).warning(
                    f"[mv] queued {queued} — func_handler init in progress "
                    f"({len(self._pending_robot_moves)} pending)"
                )
            return

        added = self._enqueue_robot_move_steps(sentence_id, normalized)
        if added:
            self.logger.bind(tag=TAG).info(
                f"[mv] enqueued {added} "
                f"(waiting={len(self._robot_move_sequence_queue)}, "
                f"in_flight={self._robot_move_in_flight})"
            )
        self._pump_robot_move_queue()

    def _dispatch_robot_move_codes(self, sentence_id: str | None, codes: list[str]) -> None:
        from core.utils.robot_move_codec import extract_move_steps

        if not codes:
            return
        steps = extract_move_steps(
            " ".join(f"mv:{c}" for c in codes),
            default_sec=self._robot_move_default_duration(),
            max_sec=self._robot_move_max_duration(),
        )
        if len(steps) != len(codes):
            from core.utils.robot_move_codec import RobotMoveStep, clamp_duration

            default_sec = self._robot_move_default_duration()
            max_sec = self._robot_move_max_duration()
            steps = [
                RobotMoveStep(
                    code=c,
                    duration_sec=0
                    if c == "s"
                    else clamp_duration(default_sec, default_sec=default_sec, max_sec=max_sec),
                )
                for c in codes
            ]
        self._dispatch_robot_move_steps(sentence_id, steps)

    async def _stream_dance_music(self, track: int, song_name: str | None = None) -> bool:
        """Stream music from ./music/ — search by song_name if specified, else preset/random."""
        from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
        from core.utils.music_library import resolve_music_path

        selected, reason = resolve_music_path(
            self,
            query=song_name,
            track=track,
            prefer_dance_track=True,
        )
        if selected is None or not selected.is_file():
            self.logger.bind(tag=TAG).warning(
                f"[mv] live dance track {track}: no music ({reason}) "
                f"song={song_name!r}"
            )
            return False

        sid = getattr(self, "sentence_id", None) or "dance"
        self.logger.bind(tag=TAG).info(
            f"[mv] streaming live dance music ({reason}): {selected.name} "
            f"track={track} song={song_name!r}"
        )
        from core.utils.music_eq_cache import get_or_analyze_music_eq
        from core.utils.music_eq_analyzer import profile_summary, timeline_playback_log

        profile = get_or_analyze_music_eq(selected)
        self._pending_dance_profile = profile
        self._pending_dance_music_path = str(selected)
        # Actual music length — cooldown / "dance done" must follow the real
        # song, not the hardcoded DANCE_MOVE_DURATION_SEC default (24s). A long
        # song (e.g. 5 min) would otherwise end the server dance window early.
        dance_duration = 0.0
        if profile is not None and getattr(profile, "timeline", ""):
            dance_duration = (
                len(profile.timeline)
                * int(getattr(profile, "segment_ms", 6000) or 6000)
            ) / 1000.0
        if dance_duration <= 0:
            try:
                from pydub import AudioSegment

                ext = selected.suffix.lstrip(".") or None
                dance_duration = len(
                    AudioSegment.from_file(str(selected), format=ext)
                ) / 1000.0
            except Exception:
                dance_duration = 0.0
        self._pending_dance_music_duration = dance_duration
        self.logger.bind(tag=TAG).info(
            f"[mv] dance music duration ≈ {dance_duration:.1f}s "
            f"({len(profile.timeline) if profile and profile.timeline else 0} segs)"
        )
        self.logger.bind(tag=TAG).info(
            f"[mv] dance EQ → {profile_summary(profile)}"
        )
        self.logger.bind(tag=TAG).info(
            f"[mv] dance EQ playback map: {timeline_playback_log(profile)}"
        )
        # FIRST + FILE + LAST — same envelope as play_music so TTS thread encodes Opus.
        self.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sid,
                sentence_type=SentenceType.FIRST,
                content_type=ContentType.ACTION,
            )
        )
        self.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sid,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.FILE,
                content_file=str(selected),
            )
        )
        self.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sid,
                sentence_type=SentenceType.LAST,
                content_type=ContentType.ACTION,
            )
        )
        return True

    def _merge_dance_eq_args(self, code: str, tool_args: dict) -> dict:
        from core.utils.music_eq_cache import get_or_analyze_music_eq
        from core.utils.music_eq_analyzer import (
            default_profile_for_track,
            profile_summary,
            timeline_playback_log,
        )
        from core.utils.robot_move_codec import dance_track_for_code, is_dance_code

        if not is_dance_code(code):
            return tool_args
        merged = dict(tool_args)
        profile = getattr(self, "_pending_dance_profile", None)
        if profile is None:
            music_path = getattr(self, "_pending_dance_music_path", None)
            if music_path:
                profile = get_or_analyze_music_eq(music_path)
        if profile is None:
            profile = default_profile_for_track(dance_track_for_code(code))
        merged.update(profile.to_mcp_dict())
        self._pending_dance_profile = None
        self._pending_dance_music_path = None
        self.logger.bind(tag=TAG).info(
            f"[mv] dance MCP mood ← EQ {profile_summary(profile)}"
        )
        if profile.timeline:
            self.logger.bind(tag=TAG).info(
                f"[mv] dance MCP timeline: {timeline_playback_log(profile)}"
            )
        return merged

    def _execute_robot_move(
        self, sentence_id: str | None, code: str, duration_sec: int = 0, song: str | None = None
    ) -> None:
        from core.utils.robot_move_codec import (
            RobotMoveStep,
            build_mcp_call,
            format_move_step,
            dance_track_for_code,
            is_dance_code,
        )

        step = RobotMoveStep(code=code, duration_sec=duration_sec, song=song)
        queue_item = (sentence_id, code, duration_sec, song)

        if getattr(self, "_robot_move_in_flight", False):
            self._robot_move_sequence_queue.insert(0, queue_item)
            return
        if not getattr(self, "func_handler", None):
            self.logger.bind(tag=TAG).warning("[mv] skip — func_handler missing")
            self._robot_move_sequence_queue.insert(0, queue_item)
            return
        if not hasattr(self, "_executed_robot_moves"):
            self._executed_robot_moves = set()

        dedupe_key = (sentence_id, code, duration_sec, song)
        if dedupe_key in self._executed_robot_moves:
            self.logger.bind(tag=TAG).debug(
                f"[mv] skip duplicate mv:{format_move_step(step)} sentence_id={sentence_id}"
            )
            self._pump_robot_move_queue()
            return

        available = self._robot_move_available_tools()
        motor_tools = sorted(n for n in available if n and "motor" in n)
        if not motor_tools:
            motor_tools = sorted(
                n for n in available if n and ("motor" in n or "chassis" in n)
            )
        self.logger.bind(tag=TAG).info(
            f"[mv] available tools={len(available)} motor/chassis={motor_tools}"
        )

        tool_name, tool_args = build_mcp_call(step, available)
        if not tool_name:
            self.logger.bind(tag=TAG).warning(
                f"[mv] mv:{format_move_step(step)} — no MCP tool yet "
                f"(available={len(available)}), will retry"
            )
            if dedupe_key not in (self._pending_robot_moves or []):
                self._pending_robot_moves.append(dedupe_key)
            self._robot_move_sequence_queue.insert(0, queue_item)
            return

        self._executed_robot_moves.add(dedupe_key)
        self._robot_move_in_flight = True

        def _dispatch_mcp_now() -> None:
            dispatch_args = self._merge_dance_eq_args(code, tool_args)
            args_json = json.dumps(dispatch_args, ensure_ascii=False)
            self.logger.bind(tag=TAG).info(
                f"[mv] dispatch mv:{format_move_step(step)} → {tool_name} "
                f"args={args_json} sentence_id={sentence_id}"
            )

            def _on_mv_tool_done(
                fut, mv_step=step, tool=tool_name, mv_duration=duration_sec
            ):
                try:
                    result = fut.result()
                    action = getattr(result, "action", None)
                    payload = getattr(result, "result", None) or getattr(
                        result, "response", None
                    )
                    self.logger.bind(tag=TAG).info(
                        f"[mv] done mv:{format_move_step(mv_step)} → {tool} "
                        f"action={action} result={payload}"
                    )
                except Exception as exc:
                    self.logger.bind(tag=TAG).error(
                        f"[mv] failed mv:{format_move_step(mv_step)} → {tool}: {exc}"
                    )

                self.clearSpeakStatus()

                is_dance = is_dance_code(mv_step.code)
                move_sec = (
                    float(mv_duration or 0)
                    if mv_step.code != "s" and mv_duration and not is_dance
                    else 0.0
                )
                dance_sec = float(mv_duration or 0) if is_dance and mv_duration else 0.0
                # Prefer the ACTUAL music length (from _stream_dance_music) over the
                # hardcoded DANCE_MOVE_DURATION_SEC default so the cooldown and the
                # "dance done" window track the real song duration.
                actual_dance_sec = float(
                    getattr(self, "_pending_dance_music_duration", 0) or 0
                )
                if is_dance and actual_dance_sec > 0:
                    dance_sec = actual_dance_sec
                settle_sec = move_sec + 0.15 if move_sec > 0 else 0.0

                def _after_physical_move() -> None:
                    self._robot_move_in_flight = False
                    if not is_dance:
                        self.reset_audio_states()
                        self.logger.bind(tag=TAG).info(
                            f"[mv] move settled — mic buffer reset "
                            f"(queued {move_sec:.1f}s on device)"
                        )
                    else:
                        self.logger.bind(tag=TAG).info(
                            f"[mv] dance queued — mic stays off on device "
                            f"(~{dance_sec:.0f}s music)"
                        )
                    self._start_robot_move_cooldown(
                        dance_sec if is_dance and dance_sec > 0 else None
                    )
                    if (
                        self._robot_move_sequence_queue
                        and not getattr(self, "_robot_move_shutdown", False)
                        and not (
                            getattr(self, "stop_event", None)
                            and self.stop_event.is_set()
                        )
                    ):
                        self.logger.bind(tag=TAG).info(
                            f"[mv] next in queue ({len(self._robot_move_sequence_queue)} waiting)"
                        )
                        self._schedule_robot_move_pump()

                mv_loop = getattr(self, "loop", None)
                wait_sec = (
                    dance_sec + 0.5 if is_dance and dance_sec > 0 else settle_sec
                )
                if wait_sec > 0 and mv_loop is not None:
                    label = "dance" if is_dance else "move"
                    self.logger.bind(tag=TAG).info(
                        f"[mv] waiting {wait_sec:.1f}s for on-device {label} before next step"
                    )
                    mv_loop.call_later(wait_sec, _after_physical_move)
                else:
                    _after_physical_move()

            future = asyncio.run_coroutine_threadsafe(
                self.func_handler.handle_llm_function_call(
                    self, {"name": tool_name, "arguments": args_json}
                ),
                self.loop,
            )
            future.add_done_callback(_on_mv_tool_done)

        if is_dance_code(code):
            track = dance_track_for_code(code)
            loop = getattr(self, "loop", None)
            if loop is None:
                self.logger.bind(tag=TAG).warning(
                    f"[mv] live dance mv:{code} — event loop missing"
                )
                _dispatch_mcp_now()
                return

            async def _stream_then_dance() -> None:
                from core.handle.sendAudioHandle import send_tts_message

                # Hold the device mic off while we fetch/stream the dance music.
                # An online download can take many seconds; a live mic would let
                # the user's next request abort the queued dance.
                try:
                    await send_tts_message(self, "start", None)
                    self.logger.bind(tag=TAG).info(
                        f"[mv] dance mic hold on — device in speaking until music ready"
                    )
                except Exception as exc:
                    self.logger.bind(tag=TAG).warning(
                        f"[mv] dance mic-hold start failed: {exc}"
                    )
                ok = await self._stream_dance_music(track, song_name=song)
                if not ok:
                    self.logger.bind(tag=TAG).error(
                        f"[mv] live dance mv:{code} — no music in ./music "
                        f"(index empty or directory missing)"
                    )
                # Let Opus frames reach the robot before MCP dance.
                await asyncio.sleep(3.0 if ok else 0.5)
                _dispatch_mcp_now()

            asyncio.run_coroutine_threadsafe(_stream_then_dance(), loop)
            return

        _dispatch_mcp_now()

    def _dispatch_robot_move_codes_now(
        self, sentence_id: str | None, codes: list[str]
    ) -> None:
        """Backward-compatible entry: enqueue then pump one-at-a-time."""
        self._dispatch_robot_move_codes(sentence_id, codes)

    def _dispatch_mv_from_assistant_text(
        self,
        sentence_id: str | None,
        text: str,
        *,
        label: str = "",
        defer_post_tts: bool = False,
    ) -> None:
        from core.utils.robot_move_codec import (
            extract_move_codes,
            extract_move_steps_from_assistant_reply,
            format_move_step,
        )

        if not text:
            return
        if not getattr(self, "_user_requested_move", False):
            self.logger.bind(tag=TAG).debug(
                f"[mv] skip ({label}): user move_intent=False this turn"
            )
            return
        allow_inference = self._robot_move_allow_inference()
        steps = extract_move_steps_from_assistant_reply(
            text,
            default_sec=self._robot_move_default_duration(),
            max_sec=self._robot_move_max_duration(),
            allow_inference=allow_inference,
        )
        if not steps:
            if allow_inference:
                return
            self.logger.bind(tag=TAG).warning(
                f"[mv] no mv:* tags in LLM reply ({label}) — robot will not move «{text[:80]}»"
            )
            return
        if allow_inference and not extract_move_codes(text):
            tag_repr = [format_move_step(s) for s in steps]
            self.logger.bind(tag=TAG).info(
                f"[mv] inferred ({label}): {tag_repr} «{text[:80]}»"
            )
        self._dispatch_robot_move_steps(
            sentence_id, steps, defer_post_tts=defer_post_tts
        )

    def _prepare_llm_text_for_tts(
        self, sentence_id: str | None, text: str, *, trim_edges: bool = False
    ) -> str:
        from core.utils.assistant_reply_tags import prepare_final_assistant_text_for_tts

        return prepare_final_assistant_text_for_tts(
            self,
            text,
            sentence_id=sentence_id,
            trim_edges=trim_edges,
        )

    def _put_tts_stream_text(
        self, sentence_id: str | None, text: str | None, *, tags_sanitized: bool = True
    ) -> None:
        if not text or not str(text).strip():
            return
        from core.utils.textUtils import strip_unwanted_scripts_for_tts
        from core.utils.tts_tag_sanitize import strip_control_tags_for_tts

        text = strip_unwanted_scripts_for_tts(str(text))
        # Final guarantee: no device/tool tag (mv:/mem:/vol:/wx:/tof:/char:/sleep)
        # may reach the TTS engine, regardless of any upstream hold/strip hiccup.
        text = strip_control_tags_for_tts(text, trim_edges=False)
        if not text.strip():
            return
        self.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT,
                content_detail=text,
                tags_sanitized=tags_sanitized,
            )
        )

    def _enqueue_tts_stream_part(
        self, sentence_id: str | None, text: str, *, flush_hold: bool = False
    ) -> None:
        """Stream-safe TTS enqueue: holds incomplete trailing control tags."""
        from core.utils.assistant_reply_tags import (
            get_tag_hold_from_conn,
            process_assistant_stream_chunk,
        )

        hold = get_tag_hold_from_conn(self)
        spoken, hold, _ = process_assistant_stream_chunk(
            self,
            text or "",
            hold,
            sentence_id=sentence_id,
            label="tts_stream",
            flush=flush_hold,
            default_mv_sec=self._robot_move_default_duration(),
            max_mv_sec=self._robot_move_max_duration(),
            allow_mv_inference=self._robot_move_allow_inference(),
        )
        self._put_tts_stream_text(sentence_id, spoken)

    def _flush_direct_answer_move_hold(
        self, tc: dict, sentence_id: str | None, da_response: str
    ) -> None:
        from core.utils.assistant_reply_tags import (
            load_tag_hold_from_tc,
            process_assistant_stream_chunk,
            save_tag_hold_to_tc,
        )

        hold = load_tag_hold_from_tc(tc)
        parsed_len = tc.get("_da_parsed_len", 0)
        tail = da_response[parsed_len:] if da_response else ""
        tc["_da_parsed_len"] = len(da_response or "")
        if not tail and not hold.merged_prefix():
            hold.clear()
            save_tag_hold_to_tc(tc, hold)
            return
        tail = self._clean_response_garbage(tail)
        spoken, hold, _ = process_assistant_stream_chunk(
            self,
            tail,
            hold,
            sentence_id=sentence_id,
            label="da_tail",
            flush=True,
            default_mv_sec=self._robot_move_default_duration(),
            max_mv_sec=self._robot_move_max_duration(),
            allow_mv_inference=self._robot_move_allow_inference(),
            apply_mem=True,
        )
        save_tag_hold_to_tc(tc, hold)
        self._put_tts_stream_text(sentence_id, spoken)

    def _put_plain_fewshot_pair(self, user: str, response: str) -> None:
        """Chèn cặp user/assistant dạng text thuần (không cần tool-call)."""
        self.dialogue.put(Message(role="user", content=user, is_temporary=True))
        self.dialogue.put(
            Message(role="assistant", content=response, is_temporary=True)
        )

    def _inject_move_fewshots(self):
        """Chèn few-shot di chuyển/nhảy dạng plain text — chạy được ở mọi intent
        mode (kể cả nointent). Mẫu tổng quát: tên bài có thể là BẤT KỲ bài nào
        người dùng nêu; server tự tìm nhạc (local/cache/online).
        (function_call mode đã có sẵn few-shot dạng tool-call riêng.)
        """
        if self.intent_type == "function_call":
            return
        locale = getattr(self, "active_locale", None) or "vi"
        if str(locale).lower() == "en":
            pairs = [
                ("Turn left", "Turning left now mv:t"),
                ("Turn right", "Turning right mv:p"),
                ("Dance for me", "Sure, dancing now mv:d"),
                (
                    "Dance to Baby Shark",
                    "Sure, dancing to Baby Shark mv:d:song=Baby Shark",
                ),
                ("Stop please", "Okay, stopping now mv:s"),
            ]
        else:
            pairs = [
                ("Kita ơi quẹo trái đi", "Mình đi sang trái nha mv:t"),
                ("Kita ơi quay phải đi", "Mình quay phải nha mv:p"),
                ("Kita ơi nhảy đi", "Okie, mình nhảy nha mv:d"),
                (
                    "Nhảy theo bài Baby Shark đi",
                    "Okie, mình nhảy theo bài Baby Shark nha mv:d:song=Baby Shark",
                ),
                ("Dừng lại đi", "Okie, mình dừng nha mv:s"),
            ]
        for user, response in pairs:
            self._put_plain_fewshot_pair(user, response)
        self.logger.bind(tag=TAG).debug(
            f"已注入 move/dance few-shot 示例 (plain text, locale={locale})"
        )

    def _inject_tool_call_fewshot(self):
        """注入工具调用 few-shot 示例到对话历史。
        结构：正样本（工具调用示例）放在动态 system 之前，可命中前缀缓存；
        负样本（直接回答示例）放在动态 system 之后、紧挨真实用户消息，
        确保模型在处理用户消息前最后看到的是"不调工具"的行为模式。
        """
        if self.intent_type != "function_call":
            return
        if not hasattr(self, "func_handler") or self.func_handler is None:
            return

        tools = self.func_handler.get_functions()
        if not tools:
            return

        tool_names = {t.get("function", {}).get("name") for t in tools}

        locale = getattr(self, "active_locale", None) or "vi"
        if str(locale).lower() == "en":
            self._inject_direct_answer_fewshot_en(tool_names)
        else:
            self._inject_direct_answer_fewshot_vi(tool_names)

        self.logger.bind(tag=TAG).debug(
            f"已注入工具调用 few-shot 示例 (locale={locale})"
        )

    def _put_direct_answer_fewshot(self, user: str, response: str, tc_id: str) -> None:
        self.dialogue.put(Message(role="user", content=user, is_temporary=True))
        self.dialogue.put(
            Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": tc_id,
                        "function": {
                            "arguments": json.dumps(
                                {"response": response}, ensure_ascii=False
                            ),
                            "name": "direct_answer",
                        },
                        "type": "function",
                        "index": 0,
                    }
                ],
                is_temporary=True,
            )
        )
        self.dialogue.put(
            Message(
                role="tool",
                tool_call_id=tc_id,
                content="已直接回复",
                is_temporary=True,
            )
        )

    def _inject_direct_answer_fewshot_vi(self, tool_names: set) -> None:
        """Vietnamese direct_answer + tag few-shot examples."""
        self._put_direct_answer_fewshot(
            "Kể chuyện đi",
            "Dạ, bạn muốn nghe truyện cổ tích, phiêu lưu hay hài nhi?",
            "fewshot_da_001",
        )
        self._put_direct_answer_fewshot(
            "Kita ơi quẹo trái",
            "Mình đi sang trái rồi nha mv:t",
            "fewshot_da_robot_001",
        )
        self._put_direct_answer_fewshot(
            "Quay phải đi",
            "Mình quay phải nha mv:p",
            "fewshot_da_robot_002",
        )
        self._put_direct_answer_fewshot(
            "Thôi mình buồn ngủ quá",
            "Ngủ ngon nha, mai chơi tiếp sleep",
            "fewshot_da_sleep_001",
        )
        self._put_direct_answer_fewshot(
            "Mình thích cà phê lắm",
            "Okie, mình nhớ bạn thích cà phê nha mem:like:coffee",
            "fewshot_da_mem_001",
        )
        self._inject_sleep_tool_fewshot_vi(tool_names)

    def _inject_direct_answer_fewshot_en(self, tool_names: set) -> None:
        """English direct_answer + tag few-shot examples."""
        self._put_direct_answer_fewshot(
            "Tell me a story",
            "Sure! Do you want a fairy tale, adventure, or something funny?",
            "fewshot_da_en_001",
        )
        self._put_direct_answer_fewshot(
            "Turn left",
            "Turning left now mv:t",
            "fewshot_da_robot_en_001",
        )
        self._put_direct_answer_fewshot(
            "Turn right",
            "Turning right mv:p",
            "fewshot_da_robot_en_002",
        )
        self._put_direct_answer_fewshot(
            "I'm getting sleepy",
            "Goodnight, see you tomorrow sleep",
            "fewshot_da_sleep_en_001",
        )
        self._put_direct_answer_fewshot(
            "I love coffee",
            "Got it, I'll remember you like coffee mem:like:coffee",
            "fewshot_da_mem_en_001",
        )
        self._put_direct_answer_fewshot(
            "Set volume to fifty percent",
            "Sure, setting volume to 50 vol:50",
            "fewshot_da_vol_en_001",
        )
        self._put_direct_answer_fewshot(
            "What's the weather in London tomorrow?",
            "Let me check tomorrow's weather in London wx:London@tomorrow",
            "fewshot_da_wx_en_001",
        )
        self._inject_sleep_tool_fewshot_en(tool_names)

    def _inject_sleep_tool_fewshot_vi(self, tool_names: set) -> None:
        if "go_to_sleep" in tool_names:
            tc_id = "fewshot_sleep_001"
            self.dialogue.put(Message(role="user", content="ngủ đi nhé", is_temporary=True))
            self.dialogue.put(Message(
                role="assistant",
                tool_calls=[{
                    "id": tc_id,
                    "function": {
                        "arguments": '{"say_goodbye": "Ngủ ngon nha! Hẹn gặp lại bạn sau 😴"}',
                        "name": "go_to_sleep",
                    },
                    "type": "function", "index": 0,
                }],
                is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="tool", tool_call_id=tc_id,
                content="sleep_intent_handled", is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="assistant",
                content="Ngủ ngon nha! Hẹn gặp lại bạn sau 😴",
                is_temporary=True,
            ))
        elif "handle_exit_intent" in tool_names:
            tc_id = "fewshot_exit_001"
            self.dialogue.put(Message(role="user", content="拜拜", is_temporary=True))
            self.dialogue.put(Message(
                role="assistant",
                tool_calls=[{
                    "id": tc_id,
                    "function": {"arguments": '{"say_goodbye": "再见，下次再聊~"}', "name": "handle_exit_intent"},
                    "type": "function", "index": 0,
                }],
                is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="tool", tool_call_id=tc_id,
                content="退出意图已处理", is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="assistant", content="再见，下次再聊~", is_temporary=True,
            ))

    def _inject_sleep_tool_fewshot_en(self, tool_names: set) -> None:
        if "go_to_sleep" in tool_names:
            tc_id = "fewshot_sleep_en_001"
            self.dialogue.put(Message(role="user", content="go to sleep", is_temporary=True))
            self.dialogue.put(Message(
                role="assistant",
                tool_calls=[{
                    "id": tc_id,
                    "function": {
                        "arguments": '{"say_goodbye": "Goodnight! See you later 😴"}',
                        "name": "go_to_sleep",
                    },
                    "type": "function", "index": 0,
                }],
                is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="tool", tool_call_id=tc_id,
                content="sleep_intent_handled", is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="assistant",
                content="Goodnight! See you later 😴",
                is_temporary=True,
            ))
        elif "handle_exit_intent" in tool_names:
            tc_id = "fewshot_exit_en_001"
            self.dialogue.put(Message(role="user", content="goodbye", is_temporary=True))
            self.dialogue.put(Message(
                role="assistant",
                tool_calls=[{
                    "id": tc_id,
                    "function": {"arguments": '{"say_goodbye": "Bye! Talk to you soon."}', "name": "handle_exit_intent"},
                    "type": "function", "index": 0,
                }],
                is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="tool", tool_call_id=tc_id,
                content="exit_intent_handled", is_temporary=True,
            ))
            self.dialogue.put(Message(
                role="assistant", content="Bye! Talk to you soon.", is_temporary=True,
            ))

    def _init_report_threads(self):
        """初始化ASR和TTS上报线程"""
        if not self.read_config_from_api or self.need_bind:
            return
        if self.chat_history_conf == 0:
            return
        if self.report_thread is None or not self.report_thread.is_alive():
            self.report_thread = threading.Thread(
                target=self._report_worker, daemon=True
            )
            self.report_thread.start()
            self.logger.bind(tag=TAG).info("TTS上报线程已启动")

    def _initialize_tts(self):
        """初始化TTS"""
        tts = None
        if not self.need_bind:
            tts = initialize_tts(self.config)

        if tts is None:
            tts = DefaultTTS(self.config, delete_audio_file=True)

        return tts

    def _initialize_asr(self):
        """初始化ASR"""
        if (
                self._asr is not None
                and hasattr(self._asr, "interface_type")
                and self._asr.interface_type == InterfaceType.LOCAL
        ):
            # 如果公共ASR是本地服务，则直接返回
            # 因为本地一个实例ASR，可以被多个连接共享
            asr = self._asr
        else:
            # 如果公共ASR是远程服务，则初始化一个新实例
            # 因为远程ASR，涉及到websocket连接和接收线程，需要每个连接一个实例
            asr = initialize_asr(self.config)

        return asr

    def _initialize_voiceprint(self):
        """为当前连接初始化声纹识别（含本地用户档案 + 多用户录入）"""
        try:
            voiceprint_config = self.config.get("voiceprint", {})
            if voiceprint_config:
                voiceprint_provider = VoiceprintProvider(voiceprint_config)
                if voiceprint_provider is not None and voiceprint_provider.enabled:
                    from core.utils.voice_user_store import VoiceUserStore

                    self.voiceprint_provider = voiceprint_provider
                    self.voice_user_store = VoiceUserStore(voiceprint_config)
                    # 预置 admin（Mr Blue）到本地映射，首次即可识别 admin
                    voiceprint_provider.add_speaker(
                        self.voice_user_store.admin_speaker_id,
                        self.voice_user_store.admin_name,
                        "Admin (Mr Blue)",
                    )
                    # 恢复历史录入的用户到本地映射
                    for sid, info in dict(
                        self.voice_user_store.users
                    ).items():
                        voiceprint_provider.add_speaker(
                            sid, info.get("name", sid), info.get("description", "")
                        )
                    self.logger.bind(tag=TAG).info(
                        f"声纹识别功能已在连接时动态启用 "
                        f"(admin={self.voice_user_store.admin_name}, "
                        f"users={len(self.voice_user_store.users)}, "
                        f"multi-user feature={self.voice_user_store.enroll_enabled})"
                    )
                else:
                    self.logger.bind(tag=TAG).warning("声纹识别功能启用但配置不完整")
            else:
                self.logger.bind(tag=TAG).info("声纹识别功能未启用")
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"声纹识别初始化失败: {str(e)}")

    def _voice_enroll_enabled(self) -> bool:
        """True when the multi-user voice feature (voiceprint.enroll_enabled) is on."""
        return bool(getattr(self.voice_user_store, "enroll_enabled", False))

    async def _background_initialize(self):
        """在后台初始化配置和组件（完全不阻塞主循环）"""
        try:
            # 异步获取差异化配置
            await self._initialize_private_config_async()
            # 在线程池中初始化组件
            self.executor.submit(self._initialize_components)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"后台初始化失败: {e}")

    async def _initialize_private_config_async(self):
        """从接口异步获取差异化配置（异步版本，不阻塞主循环）"""
        if not self.read_config_from_api:
            self.need_bind = False
            self.bind_completed_event.set()
            return
        try:
            begin_time = time.time()
            private_config = await get_private_config_from_api(
                self.config,
                self.headers.get("device-id"),
                self.headers.get("client-id", self.headers.get("device-id")),
            )
            private_config["delete_audio"] = bool(self.config.get("delete_audio", True))
            self.logger.bind(tag=TAG).info(
                f"{time.time() - begin_time} 秒，异步获取差异化配置成功: {json.dumps(filter_sensitive_info(private_config), ensure_ascii=False)}"
            )
            self.need_bind = False
            self.bind_completed_event.set()
        except DeviceNotFoundException as e:
            self.need_bind = True
            private_config = {}
        except DeviceBindException as e:
            self.need_bind = True
            self.bind_code = e.bind_code
            private_config = {}
        except Exception as e:
            self.need_bind = True
            self.logger.bind(tag=TAG).error(f"异步获取差异化配置失败: {e}")
            private_config = {}

        init_llm, init_tts, init_memory, init_intent = (
            False,
            False,
            False,
            False,
        )

        init_vad = check_vad_update(self.common_config, private_config)
        init_asr = check_asr_update(self.common_config, private_config)

        if init_vad:
            self.config["VAD"] = private_config["VAD"]
            self.config["selected_module"]["VAD"] = private_config["selected_module"][
                "VAD"
            ]
        if init_asr:
            self.config["ASR"] = private_config["ASR"]
            self.config["selected_module"]["ASR"] = private_config["selected_module"][
                "ASR"
            ]
        if private_config.get("TTS", None) is not None:
            init_tts = True
            self.config["TTS"] = private_config["TTS"]
            self.config["selected_module"]["TTS"] = private_config["selected_module"][
                "TTS"
            ]
        if private_config.get("LLM", None) is not None:
            init_llm = True
            self.config["LLM"] = private_config["LLM"]
            self.config["selected_module"]["LLM"] = private_config["selected_module"][
                "LLM"
            ]
        if private_config.get("VLLM", None) is not None:
            self.config["VLLM"] = private_config["VLLM"]
            self.config["selected_module"]["VLLM"] = private_config["selected_module"][
                "VLLM"
            ]
        if private_config.get("Memory", None) is not None:
            init_memory = True
            self.config["Memory"] = private_config["Memory"]
            self.config["selected_module"]["Memory"] = private_config[
                "selected_module"
            ]["Memory"]
        if private_config.get("Intent", None) is not None:
            init_intent = True
            self.config["Intent"] = private_config["Intent"]
            model_intent = private_config.get("selected_module", {}).get("Intent", {})
            self.config["selected_module"]["Intent"] = model_intent
            # 加载插件配置
            if model_intent != "Intent_nointent":
                plugin_from_server = private_config.get("plugins", {})
                for plugin, config_str in plugin_from_server.items():
                    plugin_from_server[plugin] = json.loads(config_str)
                self.config["plugins"] = plugin_from_server
                self.config["Intent"][self.config["selected_module"]["Intent"]][
                    "functions"
                ] = plugin_from_server.keys()
        if private_config.get("prompt", None) is not None:
            self.config["prompt"] = private_config["prompt"]
        # 获取声纹信息
        if private_config.get("voiceprint", None) is not None:
            self.config["voiceprint"] = private_config["voiceprint"]
        if private_config.get("summaryMemory", None) is not None:
            self.config["summaryMemory"] = private_config["summaryMemory"]
        if private_config.get("device_max_output_size", None) is not None:
            self.max_output_size = int(private_config["device_max_output_size"])
        if private_config.get("chat_history_conf", None) is not None:
            self.chat_history_conf = int(private_config["chat_history_conf"])
        if private_config.get("mcp_endpoint", None) is not None:
            self.config["mcp_endpoint"] = private_config["mcp_endpoint"]
        if private_config.get("context_providers", None) is not None:
            self.config["context_providers"] = private_config["context_providers"]

        # 注入替换词到 TTS 模块配置
        if private_config.get("correct_words", None) is not None:
            select_tts_module = self.config["selected_module"]["TTS"]
            self.config["TTS"][select_tts_module]["correct_words"] = private_config[
                "correct_words"
            ]

        # 使用 run_in_executor 在线程池中执行 initialize_modules，避免阻塞主循环
        try:
            modules = await self.loop.run_in_executor(
                None,  # 使用默认线程池
                initialize_modules,
                self.logger,
                private_config,
                init_vad,
                init_asr,
                init_llm,
                init_tts,
                init_memory,
                init_intent,
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"初始化组件失败: {e}")
            modules = {}
        if modules.get("tts", None) is not None:
            self.tts = modules["tts"]
        if modules.get("vad", None) is not None:
            self.vad = modules["vad"]
        if modules.get("asr", None) is not None:
            self.asr = modules["asr"]
        if modules.get("llm", None) is not None:
            self.llm = modules["llm"]
        if modules.get("intent", None) is not None:
            self.intent = modules["intent"]
        if modules.get("memory", None) is not None:
            self.memory = modules["memory"]

    def _initialize_memory(self):
        if self.memory is None:
            return
        """初始化记忆模块"""
        self.memory.init_memory(
            role_id=self.device_id,
            llm=self.llm,
            summary_memory=self.config.get("summaryMemory", None),
            save_to_file=not self.read_config_from_api,
        )

        # 获取记忆总结配置
        memory_config = self.config["Memory"]
        memory_type = self.config["Memory"][self.config["selected_module"]["Memory"]][
            "type"
        ]
        # 如果使用 nomen 或 mem_report_only，直接返回
        if memory_type == "nomem" or memory_type == "mem_report_only":
            return
        # 使用 mem_local_short 模式
        elif memory_type == "mem_local_short":
            memory_llm_name = memory_config[self.config["selected_module"]["Memory"]][
                "llm"
            ]
            if memory_llm_name and memory_llm_name in self.config["LLM"]:
                # 如果配置了专用LLM，则创建独立的LLM实例
                from core.utils import llm as llm_utils

                memory_llm_config = self.config["LLM"][memory_llm_name]
                memory_llm_type = memory_llm_config.get("type", memory_llm_name)
                memory_llm = llm_utils.create_instance(
                    memory_llm_type, memory_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"为记忆总结创建了专用LLM: {memory_llm_name}, 类型: {memory_llm_type}"
                )
                self.memory.set_llm(memory_llm)
            else:
                # 否则使用主LLM
                self.memory.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

    def _initialize_intent(self):
        try:
            self.intent_type = self.config["Intent"][
                self.config["selected_module"]["Intent"]
            ]["type"]
        except (KeyError, TypeError):
            pass
        if self.intent_type == "function_call" or self.intent_type == "intent_llm":
            self.load_function_plugin = True
        if self.intent is None:
            return
        """初始化意图识别模块"""
        # 获取意图识别配置
        intent_config = self.config["Intent"]
        intent_type = self.config["Intent"][self.config["selected_module"]["Intent"]][
            "type"
        ]

        # 如果使用 nointent，直接返回
        if intent_type == "nointent":
            return
        # 使用 intent_llm 模式
        elif intent_type == "intent_llm":
            intent_llm_name = intent_config[self.config["selected_module"]["Intent"]][
                "llm"
            ]

            if intent_llm_name and intent_llm_name in self.config["LLM"]:
                # 如果配置了专用LLM，则创建独立的LLM实例
                from core.utils import llm as llm_utils

                intent_llm_config = self.config["LLM"][intent_llm_name]
                intent_llm_type = intent_llm_config.get("type", intent_llm_name)
                intent_llm = llm_utils.create_instance(
                    intent_llm_type, intent_llm_config
                )
                self.logger.bind(tag=TAG).info(
                    f"为意图识别创建了专用LLM: {intent_llm_name}, 类型: {intent_llm_type}"
                )
                self.intent.set_llm(intent_llm)
            else:
                # 否则使用主LLM
                self.intent.set_llm(self.llm)
                self.logger.bind(tag=TAG).info("使用主LLM作为意图识别模型")

        # func_handler 在 _initialize_components 中通过 _ensure_func_handler 统一创建

    def _character_memory_auto_extract(self) -> bool:
        cfg = (self.config or {}).get("character_memory") or {}
        return bool(cfg.get("auto_extract", False))

    def _refresh_character_memory_prompt(self, user_text: str = "") -> None:
        from core.characters.character_registry import (
            get_active_character,
            get_operational_prompt,
            get_store,
        )

        character = get_active_character(self)
        if not character:
            return
        get_store(character).prepare_turn(
            self.device_id or "default",
            user_text or "",
            auto_extract=self._character_memory_auto_extract(),
        )
        enhanced = self.prompt_manager.refresh_device_prompt(
            get_operational_prompt(
                character,
                getattr(self, "active_locale", "vi"),
                enable_voiceprint_resample=self._voice_enroll_enabled(),
            ),
            self.device_id,
            self.client_ip,
            active_character=character,
            emoji_enabled=(self.features or {}).get("emoji", True),
            locale=getattr(self, "active_locale", "vi"),
        )
        if enhanced:
            self.change_system_prompt(enhanced)

    def change_system_prompt(self, prompt):
        self.prompt = prompt
        # 更新系统prompt至上下文
        self.dialogue.update_system_message(self.prompt)

    def _dialogue_bucket(self) -> str | None:
        """Bucket key for the current speaker. None = don't segment (voiceprint off)."""
        speaker = (getattr(self, "current_speaker", None) or "").strip()
        if not speaker:
            return None
        if speaker == "未知说话人":
            return _UNKNOWN_DIALOGUE_BUCKET
        return speaker

    def _active_character_name(self) -> str:
        """Name of the current character (kira/lili) — used as the context folder."""
        try:
            from core.characters.character_registry import get_active_character

            name = get_active_character(self)
            if name:
                return str(name)
        except Exception:
            pass
        return str(
            getattr(self, "active_character", None)
            or (self.config or {}).get("character")
            or "kira"
        )

    def _ensure_user_context_loaded(self, bucket: str) -> None:
        """Load a speaker's persisted conversation from disk into memory if absent."""
        if bucket == _UNKNOWN_DIALOGUE_BUCKET:
            # Unrecognized voices are ephemeral — never load/persist context for them.
            return
        if not hasattr(self, "_user_dialogues"):
            self._user_dialogues = {}
        if bucket in self._user_dialogues:
            return
        from core.utils.user_context_store import load_context

        pairs = load_context(self._active_character_name(), bucket)
        if not pairs:
            self._user_dialogues[bucket] = []
            return
        self._user_dialogues[bucket] = [
            Message(role=role, content=content) for role, content in pairs
        ]
        logger = getattr(self, "logger", None)
        if logger:
            logger.bind(tag=TAG).info(
                f"[dialogue] loaded persisted context for {bucket!r} ({len(pairs)} msgs)"
            )

    def _schedule_user_context_save(self, bucket: str) -> None:
        """Async-save a speaker's conversation if it hasn't been saved for >2 min."""
        if bucket == _UNKNOWN_DIALOGUE_BUCKET:
            # Unrecognized voices are ephemeral — never save context for them.
            return
        if not hasattr(self, "_user_context_last_saved"):
            self._user_context_last_saved = {}
        if not hasattr(self, "_user_dialogues"):
            return
        msgs = self._user_dialogues.get(bucket) or []
        pairs = [(m.role, m.content) for m in msgs if m.content]
        if not pairs:
            return
        now = time.time()
        if now - self._user_context_last_saved.get(bucket, 0) < 120:
            return
        self._user_context_last_saved[bucket] = now
        character = self._active_character_name()
        try:
            from core.utils.user_context_store import save_context

            def _worker():
                save_context(character, bucket, pairs)

            threading.Thread(target=_worker, daemon=True).start()
        except Exception as exc:
            logger = getattr(self, "logger", None)
            if logger:
                logger.bind(tag=TAG).warning(
                    f"[dialogue] context save spawn failed: {exc}"
                )

    def _switch_dialogue_for_speaker(self) -> None:
        """Keep a separate conversation history per speaker so multiple users'
        topics don't mix. Called before appending a new user turn."""
        bucket = self._dialogue_bucket()
        if bucket is None:
            return
        if not hasattr(self, "_user_dialogues"):
            self._user_dialogues = {}
        if bucket == getattr(self, "_active_dialogue_user", None):
            # Same speaker: ensure context is loaded (first turn) + periodic async save.
            self._ensure_user_context_loaded(bucket)
            self._schedule_user_context_save(bucket)
            return

        # Save the current conversation to the previous speaker's slot (+ file).
        active = getattr(self, "_active_dialogue_user", None)
        history = [
            m for m in self.dialogue.dialogue
            if m.role != "system" and not m.is_temporary
        ]
        if active:
            self._user_dialogues[active] = history
            self._schedule_user_context_save(active)

        # Clear history but keep the system message + few-shot (temporary) examples.
        self.dialogue.dialogue = [
            m for m in self.dialogue.dialogue
            if m.role == "system" or m.is_temporary
        ]

        # Load (memory or disk) and restore the new speaker's own history.
        self._ensure_user_context_loaded(bucket)
        for m in self._user_dialogues.get(bucket, []):
            self.dialogue.put(m)
        self._active_dialogue_user = bucket
        logger = getattr(self, "logger", None)
        if logger:
            logger.bind(tag=TAG).info(
                f"[dialogue] switched per-speaker context -> {bucket!r} "
                f"(history={len(self._user_dialogues.get(bucket, []))} msgs)"
            )

    def chat(self, query, depth=0):
        # 保存当前任务的sentence_id到局部变量，避免被新任务覆盖
        current_sentence_id = None

        if query is not None:
            self.last_activity_time = time.time() * 1000
            from core.utils.language_runtime import update_locale_from_user_text
            from core.utils.robot_move_codec import user_requested_robot_move
            from core.utils.volume_tag_codec import infer_volume_from_user_text
            from core.utils.tof_tag_codec import infer_tof_calibrate_from_user_text

            update_locale_from_user_text(self, query, reason="chat")
            if depth == 0:
                self._user_requested_move = user_requested_robot_move(query)
                self._user_requested_volume = infer_volume_from_user_text(query)
                self._user_requested_tof_calibrate = infer_tof_calibrate_from_user_text(query)
                self._last_dispatched_volume = None
                self._executed_tof_calibrate = None
            self.logger.bind(tag=TAG).info(
                f"大模型收到用户消息: {query} [locale={getattr(self, 'active_locale', 'vi')}]"
                + (
                    f" [move_intent={self._user_requested_move}]"
                    if depth == 0
                    else ""
                )
                + (
                    f" [volume_intent={self._user_requested_volume}]"
                    if depth == 0 and getattr(self, "_user_requested_volume", None) is not None
                    else ""
                )
                + (
                    f" [tof_cal_intent={self._user_requested_tof_calibrate}]"
                    if depth == 0
                    and getattr(self, "_user_requested_tof_calibrate", None) is not None
                    else ""
                )
            )
            from core.characters.character_registry import get_active_character

            if get_active_character(self):
                self._last_user_text = query
                self._kira_last_user_text = query

        # 为最顶层时新建会话ID和发送FIRST请求
        if depth == 0:
            if getattr(self, "_chat_active", False):
                self.logger.bind(tag=TAG).warning(
                    f"Chat already active — skipping duplicate turn: {query!r}"
                )
                return None
            self._chat_active = True
            self.client_abort = False
            if query and get_active_character(self):
                self._refresh_character_memory_prompt(query)
            current_sentence_id = str(uuid.uuid4().hex)
            self.sentence_id = current_sentence_id  # 更新共享属性
            self._executed_robot_moves = set()
            self._executed_weather_lookups = set()
            from core.utils.assistant_reply_tags import TagStreamHold

            self._tag_stream_hold = TagStreamHold()
            self._robot_move_sequence_queue = []
            self._robot_move_in_flight = False
            self._robot_move_pump_scheduled = False
            self._robot_move_cooldown_until = 0.0
            self._robot_move_shutdown = False
            self._robot_move_pump_handle = None
            # Multi-user: load the current speaker's own conversation context
            # (saving the previous speaker's) so topics don't mix.
            self._switch_dialogue_for_speaker()
            self.dialogue.put(Message(role="user", content=query))
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )
        else:
            # 递归调用时，使用当前的sentence_id
            current_sentence_id = self.sentence_id

        # 设置最大递归深度，避免无限循环，可根据实际需求调整
        MAX_DEPTH = 5
        force_final_answer = False  # 标记是否强制最终回答

        if depth >= MAX_DEPTH:
            self.logger.bind(tag=TAG).debug(
                f"已达到最大工具调用深度 {MAX_DEPTH}，将强制基于现有信息回答"
            )
            force_final_answer = True
            # 添加系统指令，要求 LLM 基于现有信息回答
            self.dialogue.put(
                Message(
                    role="user",
                    content="[系统提示] 已达到最大工具调用次数限制，请你基于目前已经获取的所有信息，直接给出最终答案。不要再尝试调用任何工具。",
                )
            )

        # Define intent functions
        functions = None
        # 达到最大深度时，禁用工具调用，强制 LLM 直接回答
        if (
                self.intent_type == "function_call"
                and hasattr(self, "func_handler")
                and not force_final_answer
        ):
            functions = list(self.func_handler.get_functions())
            # 仅在第一层调用时注入 direct_answer 虚拟工具
            # 递归调用（depth>0）不注入，避免模型在生成文本回复时再次调 direct_answer 导致循环
            if functions is not None and depth == 0:
                functions.append(DIRECT_ANSWER_TOOL)

        response_message = []

        try:
            # 使用带记忆的对话
            memory_str = None
            # 仅当query非空（代表用户询问）时查询记忆
            if self.memory is not None and query:
                future = asyncio.run_coroutine_threadsafe(
                    self.memory.query_memory(query), self.loop
                )
                memory_str = future.result()

            # 每轮都把当前说话人身份注入 system（dialogue 内附规则：只在被问及时
            # 用名字，不在每句重复称呼），确保模型不会遗忘/编造说话人名字。
            speaker_for_system = None
            cs = (self.current_speaker or "").strip()
            if cs and cs != "未知说话人":
                speaker_for_system = cs

            if self.intent_type == "function_call" and functions is not None:
                # 使用支持functions的streaming接口
                llm_responses = self.llm.response_with_functions(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str, self.config.get("voiceprint", {}), speaker_for_system
                    ),
                    functions=functions,
                    locale=getattr(self, "active_locale", "vi"),
                )
            else:
                llm_responses = self.llm.response(
                    self.session_id,
                    self.dialogue.get_llm_dialogue_with_memory(
                        memory_str, self.config.get("voiceprint", {}), speaker_for_system
                    ),
                    locale=getattr(self, "active_locale", "vi"),
                )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM 处理出错 {query}: {e}")
            if depth == 0:
                self._chat_active = False
            return None

        # 处理流式响应
        tool_call_flag = False
        # 支持多个并行工具调用 - 使用列表存储
        tool_calls_list = []  # 格式: [{"id": "", "name": "", "arguments": ""}]
        content_arguments = ""
        emotion_flag = True
        try:
            for response in llm_responses:
                if self.client_abort:
                    break
                self.last_activity_time = time.time() * 1000
                if self.intent_type == "function_call" and functions is not None:
                    content, tools_call = response
                    if "content" in response:
                        content = response["content"]
                        tools_call = None
                    if content is not None and len(content) > 0:
                        content_arguments += content

                    if not tool_call_flag and content_arguments.startswith("<tool_call>"):
                        # print("content_arguments", content_arguments)
                        tool_call_flag = True

                    if tools_call is not None and len(tools_call) > 0:
                        tool_call_flag = True
                        self._merge_tool_calls(tool_calls_list, tools_call)

                    # 流式提取 direct_answer 的 response 参数，实时送 TTS
                    for tc in tool_calls_list:
                        if tc["name"] == "direct_answer" and tc.get("arguments"):
                            da_text = self._extract_direct_answer_response(
                                tc["arguments"]
                            )
                            parsed_len = tc.get("_da_parsed_len", 0)
                            if da_text and len(da_text) > parsed_len:
                                new_part = da_text[parsed_len:]
                                new_part = self._clean_response_garbage(new_part)
                                if new_part:
                                    from core.utils.assistant_reply_tags import (
                                        load_tag_hold_from_tc,
                                        process_assistant_stream_chunk,
                                        save_tag_hold_to_tc,
                                    )

                                    hold = load_tag_hold_from_tc(tc)
                                    spoken, hold, _ = process_assistant_stream_chunk(
                                        self,
                                        new_part,
                                        hold,
                                        sentence_id=current_sentence_id,
                                        label="da_stream",
                                        flush=False,
                                        default_mv_sec=self._robot_move_default_duration(),
                                        max_mv_sec=self._robot_move_max_duration(),
                                        apply_mem=False,
                                    )
                                    tc["_da_parsed_len"] = len(da_text)
                                    save_tag_hold_to_tc(tc, hold)
                                    self._put_tts_stream_text(
                                        current_sentence_id, spoken
                                    )
                else:
                    content = response

                # 在llm回复中获取情绪表情，一轮对话只在开头获取一次
                if emotion_flag and content is not None and content.strip():
                    if (self.features or {}).get("emoji", True):
                        asyncio.run_coroutine_threadsafe(
                            textUtils.get_emotion(self, content),
                            self.loop,
                        )
                    emotion_flag = False

                if content is not None and len(content) > 0:
                    if not tool_call_flag:
                        response_message.append(content)
                        cleaned_part = self._clean_response_garbage(content)
                        if cleaned_part:
                            self._enqueue_tts_stream_part(
                                current_sentence_id, cleaned_part
                            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"LLM stream processing error: {e}")
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.MIDDLE,
                    content_type=ContentType.TEXT,
                    content_detail=get_system_error_response(self.config),
                )
            )
            if depth == 0:
                self.tts.tts_text_queue.put(
                    TTSMessageDTO(
                        sentence_id=current_sentence_id,
                        sentence_type=SentenceType.LAST,
                        content_type=ContentType.ACTION,
                    )
                )
                self._chat_active = False
            return
        # 处理function call
        if tool_call_flag:
            bHasError = False
            # 处理基于文本的工具调用格式
            if len(tool_calls_list) == 0 and content_arguments:
                a = extract_json_from_string(content_arguments)
                if a is not None:
                    try:
                        content_arguments_json = json.loads(a)
                        tool_calls_list.append(
                            {
                                "id": str(uuid.uuid4().hex),
                                "name": content_arguments_json["name"],
                                "arguments": json.dumps(
                                    content_arguments_json["arguments"],
                                    ensure_ascii=False,
                                ),
                            }
                        )
                    except Exception as e:
                        bHasError = True
                        response_message.append(a)
                else:
                    bHasError = True
                    response_message.append(content_arguments)
                if bHasError:
                    self.logger.bind(tag=TAG).error(
                        f"function call error: {content_arguments}"
                    )

            if not bHasError and len(tool_calls_list) > 0:
                # 处理 direct_answer 虚拟工具
                direct_answer_calls = [tc for tc in tool_calls_list if tc["name"] == "direct_answer"]
                real_tool_calls = [tc for tc in tool_calls_list if tc["name"] != "direct_answer"]

                if direct_answer_calls:
                    self.logger.bind(tag=TAG).debug(
                        f"模型选择 direct_answer，流式已播报，写入对话历史"
                    )
                    for tc in direct_answer_calls:
                        da_response = self._extract_direct_answer_response(
                            tc.get("arguments", "{}")
                        )
                        if da_response:
                            self._flush_direct_answer_move_hold(
                                tc, current_sentence_id, da_response
                            )
                            da_clean = self._clean_response_garbage(da_response)
                            from core.utils.tts_tag_sanitize import (
                                strip_control_tags_for_tts,
                            )

                            # History only — tags already dispatched during da_stream/da_tail.
                            da_response = strip_control_tags_for_tts(
                                da_clean, trim_edges=True
                            )
                            self.tts.store_tts_text(current_sentence_id, da_response)
                            self.dialogue.put(
                                Message(role="assistant", content=da_response)
                            )

                    if not real_tool_calls:
                        if depth == 0:
                            self.tts.tts_text_queue.put(
                                TTSMessageDTO(
                                    sentence_id=current_sentence_id,
                                    sentence_type=SentenceType.LAST,
                                    content_type=ContentType.ACTION,
                                )
                            )
                            self._chat_active = False
                        return

                    tool_calls_list = real_tool_calls

            if not bHasError and len(tool_calls_list) > 0:
                self.logger.bind(tag=TAG).debug(
                    f"检测到 {len(tool_calls_list)} 个工具调用"
                )

                # LLM 流式阶段已播报过的文本
                streamed_text = ""
                if len(response_message) > 0:
                    streamed_text = "".join(response_message)
                    self.tts.store_tts_text(current_sentence_id, streamed_text)
                    self.dialogue.put(Message(role="assistant", content=streamed_text))
                response_message.clear()

                # 收集所有工具调用的 Future
                futures_with_data = []
                for tool_call_data in tool_calls_list:
                    self.logger.bind(tag=TAG).debug(
                        f"function_name={tool_call_data['name']}, function_id={tool_call_data['id']}, function_arguments={tool_call_data['arguments']}"
                    )

                    # 使用公共方法上报工具调用
                    tool_input = json.loads(tool_call_data.get("arguments") or "{}")
                    enqueue_tool_report(self, tool_call_data['name'], tool_input)

                    future = asyncio.run_coroutine_threadsafe(
                        self.func_handler.handle_llm_function_call(
                            self, tool_call_data
                        ),
                        self.loop,
                    )
                    futures_with_data.append((future, tool_call_data, tool_input))

                # 工具调用超时时间，可配置，默认30秒
                tool_call_timeout = int(self.config.get("tool_call_timeout", 30))
                # 等待协程结束（实际等待时长为最慢的那个）
                tool_results = []

                for future, tool_call_data, tool_input in futures_with_data:
                    try:
                        result = future.result(timeout=tool_call_timeout)
                        tool_results.append((result, tool_call_data))
                        self.logger.bind(tag=TAG).info(
                            f"[tool] done {tool_call_data['name']} action={result.action} "
                            f"result={result.result or result.response}"
                        )
                        # 使用公共方法上报工具调用结果
                        enqueue_tool_report(self, tool_call_data['name'], tool_input, str(result.result) if result.result else None, report_tool_call=False)

                    except Exception as e:
                        self.logger.bind(tag=TAG).error(
                            f"工具调用超时或异常: {tool_call_data['name']}, 错误: {e}"
                        )
                        # 超时时返回错误响应，避免整个流程卡死
                        tool_results.append((
                            ActionResponse(action=Action.ERROR, result="哎呀，网络遇到点问题，请稍后再试下！"),
                            tool_call_data
                        ))
                        # 上报工具调用错误
                        enqueue_tool_report(self, tool_call_data['name'], tool_input, str(e), report_tool_call=False)

                # 统一处理工具调用结果
                if tool_results:
                    self._handle_function_result(tool_results, depth=depth, streamed_text=streamed_text)

        # 存储对话内容
        if len(response_message) > 0:
            text_buff = "".join(response_message)
            text_buff = self._prepare_llm_text_for_tts(
                current_sentence_id, text_buff, trim_edges=True
            )
            if text_buff:
                self.tts.store_tts_text(current_sentence_id, text_buff)
                self.dialogue.put(Message(role="assistant", content=text_buff))

        if depth == 0:
            from core.characters.character_registry import get_active_character, get_store

            if get_active_character(self) and query:
                character = get_active_character(self)

                assistant = ""
                if len(response_message) > 0:
                    assistant = "".join(response_message)
                elif self.dialogue.dialogue:
                    for msg in reversed(self.dialogue.dialogue):
                        if msg.role == "assistant" and msg.content:
                            assistant = msg.content
                            break
                rude = bool(
                    query
                    and any(
                        w in query.lower()
                        for w in ("đồ ngu", "ngu si", "chó", "điên", "stupid", "shut up")
                    )
                )
                get_store(character).after_turn(
                    self.device_id or "default",
                    query,
                    assistant,
                    rude=rude,
                    auto_extract=self._character_memory_auto_extract(),
                )

            self._maybe_dispatch_volume_stt_fallback(label="chat_end")
            self._maybe_dispatch_tof_stt_fallback(label="chat_end")
            self._enqueue_tts_stream_part(
                current_sentence_id, "", flush_hold=True
            )
            self.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=current_sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
            # 使用lambda延迟计算，只有在DEBUG级别时才执行get_llm_dialogue()
            self.logger.bind(tag=TAG).debug(
                lambda: json.dumps(
                    self.dialogue.get_llm_dialogue(), indent=4, ensure_ascii=False
                )
            )
            assistant_reply = "".join(response_message).strip()
            if not assistant_reply:
                for msg in reversed(getattr(self.dialogue, "dialogue", [])):
                    if getattr(msg, "role", None) == "assistant" and getattr(
                        msg, "content", ""
                    ).strip():
                        assistant_reply = msg.content.strip()
                        break
            if not assistant_reply:
                self.logger.bind(tag=TAG).warning(
                    f"LLM returned empty response for: {query!r}"
                )
            self._chat_active = False

        return True

    def _handle_function_result(self, tool_results, depth, streamed_text=""):
        need_llm_tools = []
        record_tools = []

        for result, tool_call_data in tool_results:
            if result.action in [
                Action.RESPONSE,
                Action.NOTFOUND,
                Action.ERROR,
            ]:
                text = result.response if result.response else result.result
                text = self._prepare_llm_text_for_tts(
                    self.sentence_id, text or "", trim_edges=True
                )
                if streamed_text and text in streamed_text:
                    self.logger.bind(tag=TAG).debug(
                        f"Skipping duplicate TTS for tool {tool_call_data['name']}, already streamed"
                    )
                else:
                    if text:
                        self.tts.tts_one_sentence(self, ContentType.TEXT, content_detail=text)
                        self.tts.store_tts_text(self.sentence_id, text)
                if text:
                    self.dialogue.put(Message(role="assistant", content=text))
            elif result.action == Action.REQLLM:
                need_llm_tools.append((result, tool_call_data))
            elif result.action == Action.RECORD:
                record_tools.append((result, tool_call_data))
            else:
                pass

        # Action.RECORD：写入完整工具调用链（assistant(tool_calls) → tool(result) → assistant(response)）
        # 模型从历史中学到工具调用模式，不额外调用LLM
        if record_tools:
            # 构造 assistant 消息（含 tool_calls），记录"模型调用了哪些工具"
            all_tool_calls = [
                {
                    "id": tool_call_data["id"],
                    "function": {
                        "arguments": (
                            "{}"
                            if tool_call_data["arguments"] == ""
                            else tool_call_data["arguments"]
                        ),
                        "name": tool_call_data["name"],
                    },
                    "type": "function",
                    "index": idx,
                }
                for idx, (_, tool_call_data) in enumerate(record_tools)
            ]
            self.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

            # 写入每条工具的执行结果，记录"工具返回了什么"
            for result, tool_call_data in record_tools:
                text = result.result or ""
                self.dialogue.put(
                    Message(
                        role="tool",
                        tool_call_id=(
                            str(uuid.uuid4())
                            if tool_call_data["id"] is None
                            else tool_call_data["id"]
                        ),
                        content=text,
                    )
                )

            # 用固定文本作为最终回复，补全标准三段式，保证下一条消息是 user 而非接 tool
            response_parts = []
            for result, _ in record_tools:
                resp = result.response or result.result
                if resp:
                    response_parts.append(resp)
            if response_parts:
                self.dialogue.put(Message(role="assistant", content="，".join(response_parts)))

        if need_llm_tools:
            all_tool_calls = [
                {
                    "id": tool_call_data["id"],
                    "function": {
                        "arguments": (
                            "{}"
                            if tool_call_data["arguments"] == ""
                            else tool_call_data["arguments"]
                        ),
                        "name": tool_call_data["name"],
                    },
                    "type": "function",
                    "index": idx,
                }
                for idx, (_, tool_call_data) in enumerate(need_llm_tools)
            ]
            self.dialogue.put(Message(role="assistant", tool_calls=all_tool_calls))

            for result, tool_call_data in need_llm_tools:
                text = result.result
                if text is not None and len(text) > 0:
                    self.dialogue.put(
                        Message(
                            role="tool",
                            tool_call_id=(
                                str(uuid.uuid4())
                                if tool_call_data["id"] is None
                                else tool_call_data["id"]
                            ),
                            content=text,
                        )
                    )

            self.chat(None, depth=depth + 1)

    def _report_worker(self):
        """聊天记录上报工作线程"""
        while not self.stop_event.is_set():
            try:
                # 从队列获取数据，设置超时以便定期检查停止事件
                item = self.report_queue.get(timeout=1)
                if item is None:  # 检测毒丸对象
                    break
                try:
                    # 检查线程池状态
                    if self.executor is None:
                        continue
                    # 提交任务到线程池
                    self.executor.submit(self._process_report, *item)
                except Exception as e:
                    self.logger.bind(tag=TAG).error(f"聊天记录上报线程异常: {e}")
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.bind(tag=TAG).error(f"聊天记录上报工作线程异常: {e}")

        self.logger.bind(tag=TAG).info("聊天记录上报线程已退出")

    def _process_report(self, type, text, audio_data, report_time):
        """处理上报任务"""
        try:
            # 执行异步上报（在事件循环中运行）
            asyncio.run(report(self, type, text, audio_data, report_time))
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"上报处理异常: {e}")
        finally:
            # 标记任务完成
            self.report_queue.task_done()

    def clearSpeakStatus(self):
        self.client_is_speaking = False
        self.logger.bind(tag=TAG).debug(f"清除服务端讲话状态")

    async def close(self, ws=None):
        """资源清理方法"""
        self._clear_wx_followup(reason="connection close")
        try:
            # 清理 VAD 连接资源
            if (
                    hasattr(self, "vad")
                    and self.vad
                    and hasattr(self.vad, "release_conn_resources")
            ):
                self.vad.release_conn_resources(self)

            # 清理opus解码器
            if hasattr(self, "_connection_opus_decoder"):
                try:
                    delattr(self, "_connection_opus_decoder")
                except Exception:
                    pass

            # 清理音频缓冲区
            if hasattr(self, "audio_buffer"):
                self.audio_buffer.clear()

            # 取消超时任务
            if self.timeout_task and not self.timeout_task.done():
                self.timeout_task.cancel()
                try:
                    await self.timeout_task
                except asyncio.CancelledError:
                    pass
                self.timeout_task = None

            # 取消AEC缓存清理任务
            if hasattr(self, "_aec_cache_cleanup_task") and self._aec_cache_cleanup_task and not self._aec_cache_cleanup_task.done():
                self._aec_cache_cleanup_task.cancel()
                try:
                    await self._aec_cache_cleanup_task
                except asyncio.CancelledError:
                    pass
                self._aec_cache_cleanup_task = None

            # 清理AEC缓存
            if hasattr(self, "aec_audio_cache"):
                self.aec_audio_cache.clear()
                self.aec_audio_cache_time.clear()

            # 清理工具处理器资源
            if hasattr(self, "func_handler") and self.func_handler:
                try:
                    await self.func_handler.cleanup()
                except Exception as cleanup_error:
                    self.logger.bind(tag=TAG).error(
                        f"清理工具处理器时出错: {cleanup_error}"
                    )

            # 触发停止事件（阻止新的 motor/timer 回调）
            if self.stop_event:
                self.stop_event.set()

            self._shutdown_robot_moves()

            # 清空任务队列
            self.clear_queues()

            # 关闭WebSocket连接
            try:
                if ws:
                    # 安全地检查WebSocket状态并关闭
                    try:
                        if hasattr(ws, "closed") and not ws.closed:
                            await ws.close()
                        elif hasattr(ws, "state") and ws.state.name != "CLOSED":
                            await ws.close()
                        else:
                            # 如果没有closed属性，直接尝试关闭
                            await ws.close()
                    except Exception:
                        # 如果关闭失败，忽略错误
                        pass
                elif self.websocket:
                    try:
                        if (
                                hasattr(self.websocket, "closed")
                                and not self.websocket.closed
                        ):
                            await self.websocket.close()
                        elif (
                                hasattr(self.websocket, "state")
                                and self.websocket.state.name != "CLOSED"
                        ):
                            await self.websocket.close()
                        else:
                            # 如果没有closed属性，直接尝试关闭
                            await self.websocket.close()
                    except Exception:
                        # 如果关闭失败，忽略错误
                        pass
            except Exception as ws_error:
                self.logger.bind(tag=TAG).error(f"关闭WebSocket连接时出错: {ws_error}")

            if self.tts:
                await self.tts.close()
            if self.asr:
                await self.asr.close()

            # 最后关闭线程池（避免阻塞）
            if self.executor:
                try:
                    self.executor.shutdown(wait=False)
                except Exception as executor_error:
                    self.logger.bind(tag=TAG).error(
                        f"关闭线程池时出错: {executor_error}"
                    )
                self.executor = None
            self.logger.bind(tag=TAG).info("连接资源已释放")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"关闭连接时出错: {e}")
        finally:
            # 确保停止事件被设置
            if self.stop_event:
                self.stop_event.set()

    def clear_queues(self):
        """清空所有任务队列"""
        self._clear_wx_followup(reason="abort/clear_queues")
        if self.tts:
            self.logger.bind(tag=TAG).debug(
                f"开始清理: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

            # 使用非阻塞方式清空队列
            for q in [
                self.tts.tts_text_queue,
                self.tts.tts_audio_queue,
                self.report_queue,
            ]:
                if not q:
                    continue
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break

            # 重置音频流控器（取消后台任务并清空队列）
            if hasattr(self, "audio_rate_controller") and self.audio_rate_controller:
                self.audio_rate_controller.reset()
                self.logger.bind(tag=TAG).debug("已重置音频流控器")

            self.logger.bind(tag=TAG).debug(
                f"清理结束: TTS队列大小={self.tts.tts_text_queue.qsize()}, 音频队列大小={self.tts.tts_audio_queue.qsize()}"
            )

    def reset_audio_states(self):
        """
        重置所有音频相关状态(VAD + ASR)
        """
        # Reset VAD states
        self.client_audio_buffer.clear()
        self.client_have_voice = False
        self.client_voice_stop = False
        self.client_voice_window.clear()
        self.last_is_voice = False
        self.vad_last_voice_time = 0.0
        self.vad_speech_start_time = 0.0

        # Clear ASR buffers
        self.asr_audio.clear()

        self.logger.bind(tag=TAG).debug("All audio states reset.")

    def chat_and_close(self, text):
        """Chat with the user and then close the connection"""
        try:
            # Use the existing chat method
            self.chat(text)

            # After chat is complete, close the connection
            self.close_after_chat = True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"Chat and close error: {str(e)}")

    async def _check_timeout(self):
        """检查连接超时"""
        try:
            while not self.stop_event.is_set():
                last_activity_time = self.last_activity_time
                if self.need_bind:
                    last_activity_time = self.first_activity_time

                # 检查是否超时（只有在时间戳已初始化的情况下）
                if last_activity_time > 0.0:
                    current_time = time.time() * 1000
                    if (
                        not self.close_after_chat
                        and current_time - last_activity_time
                        > self.timeout_seconds * 1000
                    ):
                        if not self.stop_event.is_set():
                            self.logger.bind(tag=TAG).info("连接超时，准备关闭")
                            # 设置停止事件，防止重复处理
                            self.stop_event.set()
                            # 使用 try-except 包装关闭操作，确保不会因为异常而阻塞
                            try:
                                await self.close(self.websocket)
                            except Exception as close_error:
                                self.logger.bind(tag=TAG).error(
                                    f"超时关闭连接时出错: {close_error}"
                                )
                        break
                # 每10秒检查一次，避免过于频繁
                await asyncio.sleep(10)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"超时检查任务出错: {e}")
        finally:
            self.logger.bind(tag=TAG).info("超时检查任务已退出")

    async def _check_aec_cache_expiry(self):
        """定期清理过期的AEC缓存"""
        try:
            while not self.stop_event.is_set():
                if hasattr(self, "aec_audio_cache") and self.aec_audio_cache:
                    current_time = time.time()
                    expired_keys = [
                        ts for ts, cache_time in list(self.aec_audio_cache_time.items())
                        if current_time - cache_time > 120  # 2分钟过期
                    ]
                    for ts in expired_keys:
                        self.aec_audio_cache.pop(ts, None)
                        self.aec_audio_cache_time.pop(ts, None)
                    if expired_keys:
                        self.logger.bind(tag=TAG).debug(f"[AEC] 清理过期缓存 {len(expired_keys)} 条")
                # 每30秒检查一次
                await asyncio.sleep(30)
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"AEC缓存清理任务出错: {e}")

    @staticmethod
    def _extract_direct_answer_response(arguments_str):
        """从 direct_answer 的参数中提取 response 值。
        优先使用 json.loads 标准解析，流式阶段 fallback 到字符串提取。
        """
        if not arguments_str:
            return ""
        # 优先尝试标准 JSON 解析（适用于完整且格式正确的 JSON）
        try:
            data = json.loads(arguments_str)
            if isinstance(data, dict) and "response" in data:
                return data["response"]
        except (json.JSONDecodeError, TypeError):
            pass
        # Fallback：流式阶段 JSON 可能不完整，使用字符串提取
        marker = '"response": "'
        idx = arguments_str.find(marker)
        if idx < 0:
            marker = '"response":"'
            idx = arguments_str.find(marker)
        if idx < 0:
            return ""
        start = idx + len(marker)
        raw = arguments_str[start:]
        # 去掉末尾的 JSON 闭合符号（如果已完整）
        if raw.endswith('"}'):
            raw = raw[:-2]
        elif raw.endswith('"'):
            raw = raw[:-1]
        # 处理 JSON 转义
        raw = raw.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
        return raw

    @staticmethod
    def _clean_response_garbage(text):
        """清理 response 中可能泄漏的 JSON 闭合符号。
        模型有时会在 response 内容中生成 JSON 闭合字符（如 ）"}} 或 '})，
        这些不是故事内容的一部分，需要去除。
        """
        if not text:
            return text
        # 清理独立一行的 JSON 闭合垃圾（如 ）"}}  '}}  "}}  }}  } ）
        _garbage_chars = frozenset('")\'}）')
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            if stripped and len(stripped) <= 8 and all(c in _garbage_chars for c in stripped):
                continue
            cleaned.append(line)
        result = '\n'.join(cleaned)
        # 清理末尾残留的 JSON 闭合符号
        result = re.sub(r'["\'}\]]+$', '', result.rstrip()).rstrip()
        from core.utils.textUtils import strip_unwanted_scripts_for_tts

        result = strip_unwanted_scripts_for_tts(result)
        # Strip leaked tool-call / markup fragments (can confuse TTS if spoken).
        result = re.sub(r"<tool_call>.*", "", result, flags=re.IGNORECASE | re.DOTALL)
        return result

    def _merge_tool_calls(self, tool_calls_list, tools_call):
        """合并工具调用列表

        Args:
            tool_calls_list: 已收集的工具调用列表
            tools_call: 新的工具调用
        """
        for tool_call in tools_call:
            tool_index = getattr(tool_call, "index", None)
            if tool_index is None:
                if tool_call.function.name:
                    # 有 function_name，说明是新的工具调用
                    tool_index = len(tool_calls_list)
                else:
                    tool_index = len(tool_calls_list) - 1 if tool_calls_list else 0

            # 确保列表有足够的位置
            if tool_index >= len(tool_calls_list):
                tool_calls_list.append({"id": "", "name": "", "arguments": ""})

            # 更新工具调用信息
            if tool_call.id:
                tool_calls_list[tool_index]["id"] = tool_call.id
            if tool_call.function.name:
                tool_calls_list[tool_index]["name"] = tool_call.function.name
            if tool_call.function.arguments:
                tool_calls_list[tool_index]["arguments"] += tool_call.function.arguments
