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
from common.model import get_llm, get_embeddings
from common.pydantic_model import (
    DispatchDecision,
    RouteDecision,
    TaskPlan,
    TaskResult,
)
from common.call_with_retry import call_with_retry
from common.state import GeoAgent, MainGraph, PoiAgent, RouteAgent
from services.mcp.amap_service import get_amap_tools
from agents.context.store import get_memory_store
from agents.context.rerank import rerank_memories, filter_by_relative_score
import time

def _completed_ids(planning: dict) -> set[str]:
    """完成的任务 ID"""
    return {
        task['task_id']
        for task in planning.get('tasks', [])
        if task.get('status') == "completed"
    }

def _ready_task(planning: dict) -> list[dict]:
    """可执行任务"""
    return [
        task
        for task in planning['tasks']
        if task['status'] == "pending"
        and all(
            dep in _completed_ids(planning)
            for dep in task["depends_on"]
        )
    ]

def _mark_status(planning: dict, task_id: str, status: str) -> dict:
    """更新任务状态"""
    return {
        **planning,
        "tasks": [
            {**task, "status": status}
            if task['task_id'] == task_id
            else task
            for task in planning.get('tasks', [])
        ],
    }

def _compact(messages: list, max_turns: int = 10) -> list:
    """只保留最近 N 轮。"""
    turns = []

    for message in messages:
        if isinstance(message, HumanMessage):
            turns.append([])

        if turns:
            turns[-1].append(message)

    return [
        message
        for turn in turns[-max_turns:]
        for message in turn
    ]

def _human_message(messages: list) -> HumanMessage | None:
    """获取最近一条用户消息"""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message

    return None

async def build_main_graph(checkpointer):
    """主图工厂"""
    def make_router(llm):
        async def router_node(state: MainGraph, config: RunnableConfig) -> Command[Literal['recall']]:
            """前置路由节点"""
            writer = get_stream_writer()

            llm_with_schema = llm.with_structured_output(
                schema=RouteDecision,
                method="json_mode",
            )
            profile = config['configurable']['user_profile']
            messages = [
                SystemMessage(content=(
                    load_prompt("router")
                    + "\n\n用户当前偏好:\n"
                    + json.dumps(profile, ensure_ascii=False, default=str)
                )),
                *_compact(state['messages']),
            ]
            try:
                decision: RouteDecision = await call_with_retry(lambda: llm_with_schema.ainvoke(messages))
                next_node = decision.next_node
                need_memory = decision.need_memory
            except Exception:
                next_node = "planning"
                need_memory = False

            writer({
                "event": "router",
                "message": (
                    "直接回答..."
                    if next_node == "answer"
                    else "任务规划..."
                ),
            })

            print(f"下一节点: {next_node}")
            print(f"是否需要检索记忆: {need_memory}")

            return Command(
                update={
                    "next_node": next_node,
                    "need_memory": need_memory,
                },
                goto="recall"
            )

        return router_node

    def make_planning(llm):
        async def planning_node(state: MainGraph, config: RunnableConfig) -> Command[Literal['supervisor', 'clarify']]:
            """写计划节点"""
            writer = get_stream_writer()
            writer({
                "event": "planning",
                "message": "正在分析任务并制定计划...",
            })

            context = (
                state.get('context') or []
                if state.get('need_memory')
                else []
            )
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
            messages = [
                SystemMessage(content=(
                    load_prompt("planning")
                    + f"用户当前偏好:\n"
                    + json.dumps(profile, ensure_ascii=False)
                )),
                *_compact(state['messages']),
                *context,
                *extra,
            ]
            llm_with_schema = llm.with_structured_output(
                schema=TaskPlan,
                method="json_mode",
            )
            try:
                response: TaskPlan = await call_with_retry(lambda: llm_with_schema.ainvoke(messages))
            except Exception as e:
                print(f"规划失败: {e}")
                return Command(
                    update={"messages": [AIMessage(content="规划失败，请稍后重试...")]},
                    goto="__end__"
                )

            print(f"计划: {json.dumps(response.model_dump(), ensure_ascii=False, indent=2)}\n")

            questions = [
                item.model_dump()
                for item in response.missing
            ]

            return Command(
                update={
                    "user_input": None,
                    "need_memory": None,
                    "planning": response.model_dump(),
                    "clarify_question": questions or None,
                    "clarify_answers": (
                        state.get('clarify_answers')
                        if questions
                        else None
                    ),
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
        action = (
            user_input.get("action")
            if isinstance(user_input, dict)
            else ""
        )

        if action == "cancel":
            return Command(
                update={
                    "messages": [
                        AIMessage(
                            content="好的，已取消本次行程安排。",
                            name="clarify_cancel",
                        )
                    ],
                    "user_input": None,
                    "clarify_answers": None,
                    "clarify_question": None,
                    "planning": None,
                },
                goto="__end__",
            )

        answers = {
            **(state.get("clarify_answers") or {}),
            **(
                user_input
                if isinstance(user_input, dict)
                else {}
            ),
        }

        return Command(
            update={
                "user_input": user_input,
                "clarify_answers": answers,
            },
            goto="planning",
        )

    def make_supervisor(llm):
        async def supervisor_node(
            state: MainGraph
        ) -> Command[Literal['planning', 'geo_agent', 'poi_agent', 'route_agent', 'respond']]:
            """主管派发任务节点"""
            writer = get_stream_writer()
            planning = state.get("planning") or {}

            if not planning:
                return Command(goto="planning")

            ready = _ready_task(planning)

            print(f"可执行任务: {json.dumps(ready, ensure_ascii=False, indent=2)}\n")

            if not ready:
                writer({
                    "event": "supervisor",
                    "message": "任务完成...",
                })
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
                except Exception as e:
                    print(f"派发失败: {e}")
                    response = DispatchDecision(
                        task_id=ready[0]['task_id'],
                        reason="派发失败",
                    )
                    writer({
                        "event": "supervisor",
                        "message": (
                            f"派发失败，默认执行 {ready[0]['task_id']}"
                        ),
                    })

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
                    writer({
                        "event": "supervisor",
                        "message": (
                            f"未选出有效任务，默认执行 {task['task_id']}"
                        ),
                    })

            # 更新状态
            planning = _mark_status(
                planning=planning,
                task_id=task['task_id'],
                status="in_progress",
            )

            print(f"任务状态: {json.dumps(planning, ensure_ascii=False, indent=2)}\n")

            writer({
                "event": "supervisor",
                "agent": task['agent'],
                "task_id": task['task_id'],
                "message": f"委派给 {task['agent']}：{task['content']}",
            })

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
                f"依赖任务结果: {json.dumps(dependency_results, ensure_ascii=False, default=str)}\n"
                f"用户当前偏好: {json.dumps(profile, ensure_ascii=False, default=str)}"
            )

            writer({
                "event": "subagent",
                "message": f"{name} 正在执行...",
            })
            start = time.perf_counter()
            try:
                response = await graph.ainvoke({
                    "messages": [HumanMessage(content=task_prompt)]
                })
                # 子任务最终结果
                task_result = response.get('task_result') or {}

            except Exception as e:
                # 校验失败 / 执行失败
                return Command(
                    update={
                        "messages": [
                            AIMessage(
                                content=f"{name} 执行失败: {e}",
                                name=name,
                            )
                        ],
                        "current_task": None,
                        "planning": _mark_status(
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
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                writer({
                    "event": "subagent",
                    "message": f"task_{task['task_id']} 完成 耗时: {round(elapsed_ms, 2)}ms"

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
                    "planning": _mark_status(
                        planning=state["planning"],
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

    def make_answer(llm):
        async def answer_node(state: MainGraph, config: RunnableConfig) -> Command[Literal['__end__']]:
            """简单回答"""
            writer = get_stream_writer()
            writer({
                "event": "answer",
                "message": "正在回答...",
            })

            context = (
                state.get('context') or []
                if state.get('need_memory')
                else []
            )
            profile = config['configurable']['user_profile']
            messages = [
                SystemMessage(content=(
                    load_prompt("answer")
                    + f"用户当前偏好:\n"
                    + json.dumps(profile, ensure_ascii=False, default=str)
                )),
                *_compact(state['messages']),
                *context,
            ]
            try:
                response = await call_with_retry(lambda: llm.ainvoke(messages))
            except Exception as e:
                print(f"回答失败: {e}")

            return Command(
                update={
                    "messages": [response],
                    "need_memory": None,
                },
                goto="__end__"
            )

        return answer_node

    def make_respond(llm):
        async def respond_node(state: MainGraph, config: RunnableConfig) -> Command[Literal['__end__']]:
            """汇总节点"""
            writer = get_stream_writer()
            writer({
                "event": "respond",
                "message": "正在整理最终回答...",
            })

            question = _human_message(state['messages'])
            planning = state.get('planning') or []
            results = state.get('results') or {}
            profile = config['configurable']['user_profile']

            messages = [
                SystemMessage(content=load_prompt("respond")),
                HumanMessage(content=(
                    f"用户问题: {question}\n"
                    f"用户偏好: {json.dumps(profile, ensure_ascii=False, default=str)}\n"
                    f"任务计划: {json.dumps(planning, ensure_ascii=False, default=str)}\n"
                    f"子任务结果: {json.dumps(results, ensure_ascii=False, default=str)}"
                ))
            ]
            try:
                response = await call_with_retry(lambda: llm.ainvoke(messages))
            except Exception as e:
                print(f"整理回答失败: {e}")

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


    def make_recall(llm):
        async def recall_node(state: MainGraph, config: RunnableConfig) -> Command[Literal['answer', 'planning']]:
            """召回记忆节点"""
            store = get_memory_store()
            writer = get_stream_writer()

            next_node = state.get('next_node') or "planning"

            if not state.get('need_memory'):
                return Command(
                    update={"next_node": None},
                    goto=next_node
                )

            writer({
                "event": "recall",
                "message": "正在寻找相关记忆..."
            })
            messages = [
                SystemMessage(content=(
                    f"""
                    把用户问题改写成独立、适合检索的查询。
                    规则：
                    - 不要编造对话里没有的信息
                    - 无法补全时保留原问题
                    - 只输出改写后的查询，不要解释
                    
                    用户问题: {_human_message(state['messages']).content}
                    """
                )),
            ]
            try:
                response = await call_with_retry(lambda: llm.ainvoke(messages))
                query = response.content
            except Exception as e:
                print(f"改写失败: {e}")
                query = _human_message(state['messages']).content

            query_vector = await get_embeddings().aembed_query(query)

            # 混合检索
            memories = store.search(
                query=query.strip(),
                query_vector=query_vector,
                user_id=config['configurable']['user_id'],
                top_k=10
            )
            # Rerank精排
            memories = rerank_memories(
                query=query.strip(),
                memories=memories,
                top_n=5,
            )
            # 阈值过滤
            memories = filter_by_relative_score(
                items=memories,
                ratio=0.6,
                min_score=0.2,
                limit=3
            )

            context = [
                SystemMessage(
                    content=f"记忆 {i + 1}:\n{memory['content']}"
                )
                for i, memory in enumerate(memories or [])
                if memory.get("content")
            ]

            return Command(
                update={
                    "context": context,
                    "next_node": None,
                },
                goto=next_node
            )

        return recall_node


    def graph(TOOLS, LLM) -> StateGraph:
        """构建图"""
        return (
            StateGraph(state_schema=MainGraph)
            .add_node("router", make_router(llm=LLM))
            .add_node("recall", make_recall(llm=LLM))
            .add_node("planning", make_planning(llm=LLM))
            .add_node("clarify", clarify_node)
            .add_node("answer", make_answer(llm=LLM))
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
            .add_edge("__start__", "router")
            .compile(checkpointer=checkpointer)
        )

    return graph(TOOLS=await get_amap_tools(), LLM=get_llm())
