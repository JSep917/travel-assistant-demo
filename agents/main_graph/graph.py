import json
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt

from agents.prompts.loader import load_prompt
from agents.subagents.subgraph import build_agent_subgraph
from common.amap_tools import AMAP_TOOLS
from common.model import get_llm
from common.pydantic_model import (
    DispatchDecision,
    TaskPlan,
    TaskResult,
)
from common.call_with_retry import call_with_retry
from common.state import GeoAgent, MainGraph, PoiAgent, RouteAgent
from services.mcp.amap_service import get_amap_tools
import time

from common.graph_utils import (
    KEEP_RECENT_TURNS,
    SUMMARY_BATCH_TURNS,
    compact_messages,
    flatten_turns,
    format_history,
    latest_human_message,
    mark_task_status,
    messages_after_turns,
    ready_tasks,
    split_turns,
)


async def build_main_graph(checkpointer):
    """主图工厂"""
    def make_summary(llm):
        async def summary_node(state: MainGraph) -> Command[Literal['planning']]:
            """按轮数滚动压缩较早对话。"""
            writer = get_stream_writer()
            turns = split_turns(state.get("messages") or [])
            done = state.get("summarized_turns") or 0

            if len(turns) < done + KEEP_RECENT_TURNS + SUMMARY_BATCH_TURNS:
                return Command(goto="planning")

            cutoff = len(turns) - KEEP_RECENT_TURNS
            old_messages = flatten_turns(turns[done:cutoff])

            if not old_messages:
                return Command(goto="planning")

            writer({
                "event": "summary",
                "message": "正在压缩较早的对话...",
            })

            messages = [
                SystemMessage(content=(
                    load_prompt("summary")
                    + "\n\n已有摘要:\n"
                    + (state.get("summary") or "无")
                )),
                HumanMessage(content=format_history(old_messages)),
            ]

            try:
                response = await call_with_retry(lambda: llm.ainvoke(messages))
                new_summary = response.content
            except Exception:
                return Command(goto="planning")

            if not new_summary:
                return Command(goto="planning")

            print(f"摘要: {new_summary}")

            return Command(
                update={
                    "summary": new_summary,
                    "summarized_turns": cutoff,
                },
                goto="planning",
            )

        return summary_node


    def make_planning(llm):
        async def planning_node(state: MainGraph, config: RunnableConfig) -> Command[Literal['supervisor', 'clarify']]:
            """写计划节点"""
            writer = get_stream_writer()
            writer({
                "event": "planning",
                "message": "正在分析任务并制定计划...",
            })

            extra = (
                [
                    HumanMessage(content=(
                    "上一轮计划:\n"
                    + json.dumps(state.get('planning') or {}, ensure_ascii=False)
                    + "\n累计补充信息:\n"
                    + json.dumps(state.get('clarify_answers') or {}, ensure_ascii=False)
                    ))
                ]
                if state.get('clarify_answers')
                else []
            )

            profile = config['configurable']['user_profile']
            summary = state.get("summary") or ""
            recent_messages = messages_after_turns(
                state.get("messages") or [],
                state.get("summarized_turns") or 0,
            )

            messages = [
                SystemMessage(content=(
                    load_prompt("planning")
                    + f"用户当前偏好: {json.dumps(profile, ensure_ascii=False)}\n"
                    + (f"历史摘要: {summary}\n" if summary else "无")
                )),
                *compact_messages(recent_messages, max_turns=KEEP_RECENT_TURNS),
                *extra,
            ]
            llm_with_schema = llm.with_structured_output(
                schema=TaskPlan,
                method="json_mode",
            )
            try:
                response: TaskPlan = await call_with_retry(lambda: llm_with_schema.ainvoke(messages))
            except Exception:
                return Command(
                    update={"messages": [AIMessage(content="制定计划失败，请稍后重试...")]},
                    goto="__end__"
                )

            print(f"计划: {json.dumps(response.model_dump(), ensure_ascii=False, indent=2)}\n")

            clarify_question = [
                item.model_dump()
                for item in response.missing
            ]

            return Command(
                update={
                    "planning": response.model_dump(),
                    "clarify_question": clarify_question or None,
                    "clarify_answers": state.get('clarify_answers') if clarify_question else None,
                },
                goto="clarify" if response.missing else "supervisor"
            )

        return planning_node

    async def clarify_node(state) -> Command[Literal['planning', '__end__']]:
        """人工介入补全信息节点"""
        questions = state.get("clarify_question") or []

        user_input = interrupt({
            "questions": questions,
            "allow_skip": True,
            "allow_cancel": True,
        })
        action = user_input.get('action') or 'confirm'

        content = "，".join([
            question.get('reason')
            for question in questions
        ])

        if action == "cancel":
            return Command(
                update={
                    "messages": [AIMessage(content=content, name="clarify_cancel")],
                    "clarify_answers": None,
                    "clarify_question": None,
                    "planning": None,
                },
                goto="__end__",
            )

        answers = {
            **(state.get("clarify_answers") or {}),
            **user_input
        }

        return Command(
            update={"clarify_answers": answers},
            goto="planning",
        )

    def make_supervisor(llm):
        async def supervisor_node(
            state: MainGraph
        ) -> Command[Literal['planning', 'geo_agent', 'poi_agent', 'route_agent', 'respond']]:
            """主管派发任务节点"""
            planning = state.get("planning") or {}

            if not planning:
                return Command(goto="planning")

            ready = ready_tasks(planning)

            print(f"可执行任务: {json.dumps(ready, ensure_ascii=False, indent=2)}\n")

            if not ready:
                return Command(goto="respond")

            if len(ready) == 1:
                task = {**ready[0], "status": "in_progress"}
            else:
                messages = [
                    SystemMessage(content=(
                        load_prompt("supervisor")
                        + "\n\n当前计划:\n"
                        + json.dumps(planning, ensure_ascii=False)
                        + "\n\n可执行任务 ID: "
                        + json.dumps(
                            [task['task_id'] for task in ready],
                            ensure_ascii=False,
                        ))
                    ),
                ]
                llm_with_schema = llm.with_structured_output(
                    schema=DispatchDecision,
                    method="json_mode",
                )
                try:
                    response: DispatchDecision = await call_with_retry(lambda: llm_with_schema.ainvoke(messages))
                except Exception:
                    response = DispatchDecision(
                        task_id=ready[0]['task_id'],
                        reason="派发失败",
                    )

                task = next(
                    (
                        {**item, "status": "in_progress"}
                        for item in ready
                        if item['task_id'] == response.task_id
                    ),
                    None,
                )

                if task is None:
                    # 无效任务 ID
                    task = {**ready[0], "status": "in_progress"}

            # 更新状态
            planning = mark_task_status(
                planning=planning,
                task_id=task['task_id'],
                status="in_progress",
            )

            print(f"任务状态: {json.dumps(planning, ensure_ascii=False, indent=2)}\n")

            return Command(
                update={
                    "current_task": task,
                    "planning": planning,
                },
                goto=task['agent'],
            )

        return supervisor_node

    def make_subagent(graph, name: str):
        async def subagent(state: MainGraph, config: RunnableConfig) -> Command[Literal['supervisor']]:
            """构建子图节点"""
            writer = get_stream_writer()
            task = state.get('current_task')

            if not task:
                return Command(goto="supervisor")

            # 依赖任务执行结果
            dependency_results = {
                dep: (state.get('results') or {}).get(dep)
                for dep in task['depends_on']
            }
            profile = config['configurable']['user_profile']
            # 拼接交接任务信息
            task_prompt = (
                f"任务 ID: {task['task_id']}\n"
                f"任务目标: {task['content']}\n"
                f"依赖任务: {json.dumps(task['depends_on'], ensure_ascii=False)}\n"
                f"依赖任务结果: {json.dumps(dependency_results, ensure_ascii=False)}\n"
                f"用户当前偏好: {json.dumps(profile, ensure_ascii=False)}"
            )

            trace_base = {
                "event": "trace",
                "task_id": task["task_id"],
                "agent": name,
            }

            writer({
                "event": "subagent",
                "message": f"{name} 正在执行...",
            })
            writer({**trace_base, "phase": "start"})

            start = time.perf_counter()
            try:
                response = await graph.ainvoke({
                    "messages": [HumanMessage(content=task_prompt)]
                })
                # 子任务最终结果
                task_result = response.get('task_result') or {}

            except Exception as e:
                # 校验失败 / 执行失败
                elapsed_ms = (time.perf_counter() - start) * 1000
                writer({
                    **trace_base,
                    "phase": "failed",
                    "duration_ms": round(elapsed_ms),
                    "error": str(e),
                })
                return Command(
                    update={
                        "messages": [
                            AIMessage(
                                content=f"{name} 执行失败: {e}",
                                name=name,
                            )
                        ],
                        "current_task": None,
                        "planning": mark_task_status(
                            planning=state["planning"],
                            task_id=task["task_id"],
                            status="failed",
                        ),
                        "results": {
                            **(state.get("results") or {}),
                            task["task_id"]: TaskResult(
                                task_id=task["task_id"],
                                status="failed",
                                summary=f"{name} 执行失败",
                                data={},
                                error=str(e),
                            ).model_dump(),
                        },
                    },
                    goto="supervisor",
                )

            elapsed_ms = (time.perf_counter() - start) * 1000
            writer({
                **trace_base,
                "phase": "success"
                if task_result.get("status") == "success"
                else "failed",
                "duration_ms": round(elapsed_ms),
                "error": task_result.get("error"),
            })

            return Command(
                update={
                    "messages": [
                        AIMessage(
                            content=task_result['summary'],
                            name=name,
                        )
                    ],
                    "current_task": None,
                    "planning": mark_task_status(
                        planning=state.get('planning'),
                        task_id=task["task_id"],
                        status="completed"
                        if task_result['status'] == "success"
                        else "failed",
                    ),
                    "results": {
                        **(state.get("results") or {}),
                        task["task_id"]: task_result,
                    },
                },
                goto="supervisor",
            )

        return subagent

    def make_respond(llm):
        async def respond_node(state: MainGraph, config: RunnableConfig) -> Command[Literal['__end__']]:
            """汇总节点"""
            writer = get_stream_writer()
            writer({
                "event": "respond",
                "message": "正在整理最终回答...",
            })

            question = latest_human_message(state['messages'])
            planning = state.get('planning') or []
            results = state.get('results') or {}
            profile = config['configurable']['user_profile']
            summary = state.get("summary") or ""
            recent_messages = messages_after_turns(
                state.get("messages") or [],
                state.get("summarized_turns") or 0,
            )

            messages = [
                SystemMessage(content=(
                    load_prompt("respond")
                    + f"\n用户问题: {question}\n"
                    + f"用户偏好: {json.dumps(profile, ensure_ascii=False)}\n"
                    + (f"历史摘要: {summary}\n" if summary else "无")
                    + f"任务计划: {json.dumps(planning, ensure_ascii=False)}\n"
                    + f"子任务结果: {json.dumps(results, ensure_ascii=False)}"
                )),
                *compact_messages(recent_messages, max_turns=KEEP_RECENT_TURNS),
            ]
            try:
                response = await call_with_retry(lambda: llm.ainvoke(messages))
            except Exception:
                response = AIMessage(content="整理回答失败，请稍后重试...")

            return Command(
                update={
                    "messages": [response],
                    "current_task": None,
                    "user_input": None,
                    "clarify_question": None,
                    "planning": None,
                    "results": None,
                    "context": None,
                },
                goto="__end__",
            )

        return respond_node


    def graph(TOOLS, LLM) -> StateGraph:
        """构建图"""
        return (
            StateGraph(state_schema=MainGraph)
            .add_node("summary", make_summary(llm=LLM))
            .add_node("planning", make_planning(llm=LLM))
            .add_node("clarify", clarify_node)
            .add_node("supervisor", make_supervisor(llm=LLM))
            .add_node("respond", make_respond(llm=LLM))
            .add_node(
                "geo_agent",
                make_subagent(
                    graph=build_agent_subgraph(
                        llm=LLM,
                        state=GeoAgent,
                        tools=[TOOLS[name] for name in AMAP_TOOLS["geo"]],
                        system_prompt=load_prompt("geo_agent"),
                    ),
                    name="geo_agent",
                ),
            )
            .add_node(
                "poi_agent",
                make_subagent(
                    graph=build_agent_subgraph(
                        llm=LLM,
                        state=PoiAgent,
                        tools=[TOOLS[name] for name in AMAP_TOOLS["poi"]],
                        system_prompt=load_prompt("poi_agent"),
                    ),
                    name="poi_agent",
                ),
            )
            .add_node(
                "route_agent",
                make_subagent(
                    graph=build_agent_subgraph(
                        llm=LLM,
                        state=RouteAgent,
                        tools=[TOOLS[name] for name in AMAP_TOOLS["route"]],
                        system_prompt=load_prompt("route_agent"),
                    ),
                    name="route_agent",
                ),
            )
            .add_edge("__start__", "summary")
            .add_edge("summary", "planning")
            .compile(checkpointer=checkpointer)
        )

    return graph(TOOLS=await get_amap_tools(), LLM=get_llm())
