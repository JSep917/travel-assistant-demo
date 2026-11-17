class CheckpointManager:
    """LangGraph checkpointer"""
    def __init__(self, checkpointer):
        self.checkpointer = checkpointer

    async def setup(self):
        """初始化/迁移 checkpoint 相关表"""
        await self.checkpointer.setup()

    async def delete_thread(self, thread_id: str):
        """删除指定 thread 的全部 checkpoint 数据。"""
        await self.checkpointer.adelete_thread(thread_id)