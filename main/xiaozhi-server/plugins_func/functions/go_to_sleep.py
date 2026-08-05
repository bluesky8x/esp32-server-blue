"""End conversation and put the robot into sleep mode."""

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

go_to_sleep_function_desc = {
    "type": "function",
    "function": {
        "name": "go_to_sleep",
        "description": (
            "Call when the user wants to end the conversation or put the robot to sleep. "
            "Prefer appending the `sleep` tag at end of reply when using nointent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "say_goodbye": {
                    "type": "string",
                    "description": "Short warm farewell in the user's language (vi or en).",
                }
            },
            "required": ["say_goodbye"],
        },
    },
}


@register_function("go_to_sleep", go_to_sleep_function_desc, ToolType.SYSTEM_CTL)
def go_to_sleep(conn: "ConnectionHandler", say_goodbye: str | None = None):
    try:
        from core.utils.sleep_tag_codec import trigger_sleep_mode
        from core.utils.sleep_farewell import pick_sleep_farewell
        from core.characters.character_registry import get_active_character

        if say_goodbye is None or not str(say_goodbye).strip():
            char_id = get_active_character(conn) or "kira"
            say_goodbye = pick_sleep_farewell(conn, char_id)

        trigger_sleep_mode(conn, label="go_to_sleep_tool")
        logger.bind(tag=TAG).info(f"go_to_sleep: {say_goodbye}")
        return ActionResponse(
            action=Action.RESPONSE,
            result="sleep_intent_handled",
            response=say_goodbye,
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"go_to_sleep error: {e}")
        return ActionResponse(action=Action.NONE, result="sleep_failed", response="")
