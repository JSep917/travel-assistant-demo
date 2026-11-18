from typing import Literal

from jsonschema import validate
from pydantic import ValidationError
from langchain_core.messages import (
    SystemMessage,
    ToolMessage,
    HumanMessage
)
from langgraph.graph import StateGraph
from langgraph.types import Command

from common.pydantic_model import TaskResult
from common.call_with_retry import call_with_retry
from services.redis.tool_cache import get_tool_cache
import json

def _tool_message(
    ok: bool,
    name: str,
    call_id: str,
    content: str,
    retryable: bool = False,
) -> ToolMessage:
    return ToolMessage(
        content=json.dumps({
            "ok" : ok,
            "retryable": retryable,
            "content": json.loads(content)
        }, ensure_ascii=False),
        name=name,
        tool_call_id=call_id,
        additional_kwargs={"retryable": retryable}
    )

def build_agent_subgraph(llm, state, tools: list, system_prompt: str) -> StateGraph:
    """子图工厂：agent -> execute -> agent，结束后 extract。"""

    async def execute_node(state) -> Command[Literal['agent']]:
        """工具执行节点"""
        tools_map = {tool.name: tool for tool in tools}
        calls = state["messages"][-1].tool_calls
        execute_retry = state.get("execute_retry") or 0
        failed = []
        success = []

        for call in calls:
            name = call["name"]
            args = call["args"]
            call_id = call["id"]
            tool = tools_map.get(name)
            schema = getattr(tool, "args_schema", None)

            if tool is None:
                failed.append(
                    _tool_message(
                        ok=False,
                        name=name,
                        call_id=call_id,
                        content=f"工具 {name} 不存在",
                        retryable=True
                    )
                )
                continue

            try:
                # 校验参数
                validate(instance=args, schema=schema)
                # 读取缓存
                cahce = get_tool_cache()
                cahce_key = cahce.make_key(
                    tool_name=name,
                    args=args,
                )
                tool_cache: str = await cahce.get(cahce_key)

                # 未命中缓存则执行工具
                result: list | str = await tool.ainvoke(args) if not tool_cache else tool_cache

                if isinstance(result, list):
                    result: str = result[0]['text']

                try:
                    json.loads(result)
                    # 解析成功 - 业务正常 - 写入缓存
                    if not tool_cache:
                        await cahce.set(
                            key=cahce_key,
                            content=result,
                            ttl=3600
                        )
                except json.JSONDecodeError as e:
                    print(f"业务失败: {e}")
                    pass

                if tool_cache:
                    print(f"工具 {name} 命中缓存")

            except ValidationError as e:
                failed.append(
                    _tool_message(
                        ok=False,
                        name=name,
                        call_id=call_id,
                        content=f"工具 {name} 参数不合法: {e}",
                        retryable=True
                    )
                )
                continue
            except Exception as e:
                failed.append(
                    _tool_message(
                        ok=False,
                        name=name,
                        call_id=call_id,
                        content=f"工具 {name} 执行失败: {e}",
                        retryable=True
                    )
                )
                continue

            success.append(
                _tool_message(
                    ok=True,
                    name=name,
                    call_id=call_id,
                    content=result,
                )
            )

        return Command(
            update={
                "messages": success + failed,
                "execute_retry": execute_retry + 1 if failed else 0,
            },
            goto="agent",
        )

    async def agent_node(state) -> Command[Literal['execute', 'extract']]:
        """agent 节点"""
        execute_retry = state.get("execute_retry") or 0

        messages = [
            SystemMessage(content=system_prompt),
            *state['messages'],
        ]
        llm_with_tools = llm.bind_tools(tools)
        try:
            response = await call_with_retry(lambda: llm_with_tools.ainvoke(messages))
        except Exception as e:
            print(f"任务执行失败: {e}")

        return Command(
            update={"messages": [response]},
            goto=(
                "execute"
                if response.tool_calls
                and execute_retry < 2
                else "extract"
            )
        )

    async def extract_node(state) -> Command[Literal['__end__', 'agent']]:
        """解析 JSON 节点"""
        extract_retry = state.get("extract_retry") or 0

        try:
            response: TaskResult = TaskResult.model_validate_json(state['messages'][-1].content)

            print(f"解析结果: {json.dumps(response.model_dump(exclude_none=True), ensure_ascii=False, indent=2)}")

            return Command(
                update={
                    "task_result": response.model_dump(exclude_none=True),
                    "extract_retry": 0,
                },
                goto="__end__",
            )
        except ValidationError as e:
            if extract_retry > 1:
                # 超最大限重试 交给父图处理
                raise
            return Command(
                update={
                    "messages": [
                        HumanMessage(content=(
                            f"上一条最终消息不是合法 JSON: {e}"
                            "不要再调用工具，只重新输出 TaskResult JSON"
                        ))
                    ],
                    "extract_retry": extract_retry + 1,
                },
                goto="agent"
            )

    def graph() -> StateGraph:
        return (
            StateGraph(state_schema=state)
            .add_node("agent", agent_node)
            .add_node("execute", execute_node)
            .add_node("extract", extract_node)
            .add_edge("__start__", "agent")
            .compile()
        )

    return graph()
