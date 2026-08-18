import os
import re
import time
import random
import traceback
from pathlib import Path
from core.providers.tts.dto.dto import TTSMessageDTO, SentenceType, ContentType
from core.utils.music_library import (
    extract_song_query_from_user_text,
    refresh_music_index,
    resolve_music_path,
)
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__

MUSIC_CACHE = {}

play_music_function_desc = {
    "type": "function",
    "function": {
        "name": "play_music",
        "description": "当用户要求播放音乐、歌曲时调用。支持本地歌曲与联网搜索。",
        "parameters": {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": "歌曲名称，如果用户没有指定具体歌名则为'random', 明确指定的时返回音乐的名字 示例: ```用户:播放两只老虎\n参数：两只老虎``` ```用户:播放音乐 \n参数：random ```",
                }
            },
            "required": ["song_name"],
        },
    },
}


@register_function("play_music", play_music_function_desc, ToolType.SYSTEM_CTL)
async def play_music(conn: "ConnectionHandler", song_name: str):
    try:
        music_intent = (
            f"播放音乐 {song_name}" if song_name != "random" else "随机播放音乐"
        )
        await handle_music_command(conn, music_intent)
        return ActionResponse(
            action=Action.RECORD, result="指令已接收", response="正在为您播放音乐"
        )
    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"处理音乐意图错误: {e}")
        return ActionResponse(
            action=Action.RESPONSE, result=str(e), response="播放音乐时出错了"
        )


def _extract_song_name(text):
    """从用户输入中提取歌名"""
    hit = extract_song_query_from_user_text(text)
    if hit:
        return hit
    for keyword in ["播放音乐", "phát nhạc", "bật nhạc", "play music"]:
        if keyword in text.lower():
            parts = re.split(re.escape(keyword), text, flags=re.IGNORECASE)
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()
    return None


def get_music_files(music_dir, music_ext):
    music_dir = Path(music_dir)
    music_files = []
    music_file_names = []
    for file in music_dir.rglob("*"):
        if file.is_file():
            ext = file.suffix.lower()
            if ext in music_ext:
                music_files.append(str(file.relative_to(music_dir)))
                music_file_names.append(
                    os.path.splitext(str(file.relative_to(music_dir)))[0]
                )
    return music_files, music_file_names


def initialize_music_handler(conn: "ConnectionHandler"):
    global MUSIC_CACHE
    if MUSIC_CACHE == {}:
        plugins_config = conn.config.get("plugins", {})
        if "play_music" in plugins_config:
            MUSIC_CACHE["music_config"] = plugins_config["play_music"]
            MUSIC_CACHE["music_dir"] = os.path.abspath(
                MUSIC_CACHE["music_config"].get("music_dir", "./music")
            )
            MUSIC_CACHE["music_ext"] = MUSIC_CACHE["music_config"].get(
                "music_ext", (".mp3", ".wav", ".p3")
            )
            MUSIC_CACHE["refresh_time"] = MUSIC_CACHE["music_config"].get(
                "refresh_time", 60
            )
        else:
            MUSIC_CACHE["music_dir"] = os.path.abspath("./music")
            MUSIC_CACHE["music_ext"] = (".mp3", ".wav", ".p3")
            MUSIC_CACHE["refresh_time"] = 60
        # 获取音乐文件列表
        MUSIC_CACHE["music_files"], MUSIC_CACHE["music_file_names"] = get_music_files(
            MUSIC_CACHE["music_dir"], MUSIC_CACHE["music_ext"]
        )
        MUSIC_CACHE["scan_time"] = time.time()
    return MUSIC_CACHE


async def handle_music_command(conn: "ConnectionHandler", text):
    """Handle play-music intent: search local ./music by name, or search online, else random."""
    global MUSIC_CACHE
    clean_text = re.sub(r"[^\w\s\u00C0-\u1EF9]", " ", text or "", flags=re.UNICODE)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    conn.logger.bind(tag=TAG).debug(f"检查是否是音乐命令: {clean_text}")

    query = _extract_song_name(clean_text) or _extract_song_name(text or "")
    if query and query.lower() in ("random", "ngẫu nhiên", "ngau nhien"):
        query = None

    selected_path, reason = resolve_music_path(conn, query=query, allow_online_search=True)
    if selected_path is None or not selected_path.is_file():
        conn.logger.bind(tag=TAG).error(
            f"音乐不可用或无文件 (reason={reason})"
        )
        return False

    conn.logger.bind(tag=TAG).info(
        f"播放音乐 ({reason}): {selected_path.name} query={query!r}"
    )
    await play_local_music(conn, specific_file=str(selected_path))
    return True


def _get_random_play_prompt(song_name):
    """生成播放引导语"""
    clean_name = os.path.splitext(song_name)[0]
    clean_name = clean_name.split(" [")[0] if " [" in clean_name else clean_name
    prompts = [
        f"Đang phát bài 《{clean_name}》",
        f"Mời bạn nghe bài 《{clean_name}》",
        f"Sau đây là ca khúc 《{clean_name}》",
        f"Cùng thưởng thức bài hát 《{clean_name}》 nhé",
    ]
    return random.choice(prompts)


async def play_local_music(conn: "ConnectionHandler", specific_file=None):
    global MUSIC_CACHE
    """播放音乐文件（支持本地和在线下载缓存）"""
    try:
        refresh_music_index(conn)

        if specific_file:
            if os.path.isabs(specific_file) or os.path.exists(specific_file):
                music_path = specific_file
                selected_music = os.path.basename(specific_file)
            else:
                selected_music = specific_file
                music_path = os.path.join(MUSIC_CACHE["music_dir"], specific_file)
        else:
            if not MUSIC_CACHE.get("music_files"):
                conn.logger.bind(tag=TAG).error("未找到音乐文件")
                return
            selected_music = random.choice(MUSIC_CACHE["music_files"])
            music_path = os.path.join(MUSIC_CACHE["music_dir"], selected_music)

        if not os.path.exists(music_path):
            conn.logger.bind(tag=TAG).error(f"选定的音乐文件不存在: {music_path}")
            return

        text = _get_random_play_prompt(selected_music)
        conn.tts.store_tts_text(conn.sentence_id, text)

        if conn.intent_type == "intent_llm":
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=conn.sentence_id,
                    sentence_type=SentenceType.FIRST,
                    content_type=ContentType.ACTION,
                )
            )
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT,
                content_detail=text,
            )
        )
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.FILE,
                content_file=music_path,
            )
        )
        if conn.intent_type == "intent_llm":
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=conn.sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )

    except Exception as e:
        conn.logger.bind(tag=TAG).error(f"播放音乐失败: {str(e)}")
        conn.logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")
