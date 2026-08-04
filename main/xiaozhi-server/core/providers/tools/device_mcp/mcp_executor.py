"""设备端MCP工具执行器"""

from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from config.logger import setup_logging
from ..base import ToolType, ToolDefinition, ToolExecutor
from plugins_func.register import Action, ActionResponse
from .mcp_handler import call_mcp_tool

TAG = __name__
logger = setup_logging()


class DeviceMCPExecutor(ToolExecutor):
    """设备端MCP工具执行器"""

    def __init__(self, conn):
        self.conn = conn

    async def execute(
        self, conn: "ConnectionHandler", tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        """执行设备端MCP工具"""
        if not hasattr(conn, "mcp_client") or not conn.mcp_client:
            return ActionResponse(
                action=Action.ERROR,
                response="设备端MCP客户端未初始化",
            )

        if not await conn.mcp_client.is_ready():
            return ActionResponse(
                action=Action.ERROR,
                response="设备端MCP客户端未准备就绪",
            )

        try:
            # 转换参数为JSON字符串
            import json

            logger.bind(tag=TAG).info(
                f"[tool] device_mcp → {tool_name} args={arguments}"
            )

            args_str = json.dumps(arguments) if arguments else "{}"

            # 调用设备端MCP工具
            result = await call_mcp_tool(conn, conn.mcp_client, tool_name, args_str)
            logger.bind(tag=TAG).info(
                f"[tool] device_mcp ← {tool_name} raw={str(result)[:200]}"
            )

            resultJson = None
            if isinstance(result, str):
                try:
                    resultJson = json.loads(result)
                except Exception:
                    pass

            # 视觉大模型：action 为 Action 枚举名（RESPONSE / REQLLM 等）
            if (
                resultJson is not None
                and isinstance(resultJson, dict)
                and "action" in resultJson
                and isinstance(resultJson["action"], str)
                and resultJson["action"] in Action.__members__
            ):
                return ActionResponse(
                    action=Action[resultJson["action"]],
                    response=resultJson.get("response", ""),
                )

            # 设备/模拟器：action 为业务字段（turn_left, forward…）或 success=true
            if resultJson is not None and isinstance(resultJson, dict):
                if resultJson.get("success") is True or resultJson.get("simulated") is True:
                    return ActionResponse(action=Action.NONE, result=resultJson)

            return ActionResponse(action=Action.REQLLM, result=str(result))

        except ValueError as e:
            return ActionResponse(action=Action.NOTFOUND, response=str(e))
        except Exception as e:
            return ActionResponse(action=Action.ERROR, response=str(e))

    def get_tools(self) -> Dict[str, ToolDefinition]:
        """获取所有设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return {}

        tools = {}
        mcp_tools = self.conn.mcp_client.get_available_tools()

        for tool in mcp_tools:
            func_def = tool.get("function", {})
            tool_name = func_def.get("name", "")

            if tool_name:
                tools[tool_name] = ToolDefinition(
                    name=tool_name, description=tool, tool_type=ToolType.DEVICE_MCP
                )

        return tools

    def has_tool(self, tool_name: str) -> bool:
        """检查是否有指定的设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return False

        return self.conn.mcp_client.has_tool(tool_name)
