import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.main_graph.graph import build_main_graph
from langgraph.checkpoint.memory import InMemorySaver


async def main():

    graph = await build_main_graph(checkpointer=InMemorySaver())

    drawable = graph.get_graph(xray=True)
    mermaid = drawable.draw_mermaid()

    print(mermaid)

    output = ROOT / "graph.mmd"
    output.write_text(mermaid, encoding="utf-8")
    print(f"\n已写入: {output}")

    try:
        png = drawable.draw_mermaid_png()
        png_path = ROOT / "graph.png"
        png_path.write_bytes(png)
        print(f"已写入: {png_path}")
    except Exception as exc:
        print(f"\nPNG 生成失败: {exc}")
        print("可把 graph.mmd 粘贴到 https://mermaid.live 查看")


if __name__ == "__main__":

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    asyncio.run(main())
