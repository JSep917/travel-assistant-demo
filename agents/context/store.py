from pymilvus import (
    MilvusClient,
    DataType,
    Function,
    FunctionType,
    AnnSearchRequest,
    RRFRanker,
    MilvusException,
)

from config import settings
from functools import cache
import threading


class MemoryStore:
    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self.client = MilvusClient(
            url=settings.MILVUS_URI,
            token=settings.MILVUS_TOKEN,
        )
        self._ready = False
        self._lock = threading.Lock()

    def _build_schema(self):
        """构建schema"""
        schema = self.client.create_schema(enable_dynamic_field=True)

        schema.add_field(
            field_name="memory_id",
            datatype=DataType.VARCHAR,
            max_length=1024,
            is_primary=True,
        )
        schema.add_field(
            field_name="thread_id",
            datatype=DataType.VARCHAR,
            max_length=512,
        )
        schema.add_field(
            field_name="user_id",
            datatype=DataType.VARCHAR,
            max_length=128,
        )
        schema.add_field(
            field_name="content",
            datatype=DataType.VARCHAR,
            max_length=65535,
        )
        schema.add_field(
            field_name="created_at",
            datatype=DataType.INT64,
        )
        # 稠密向量
        schema.add_field(
            field_name="dense",
            datatype=DataType.FLOAT_VECTOR,
            dim=settings.EMBEDDING_DIM,
        )
        # BM25 输入文本
        schema.add_field(
            field_name="memory_card",
            datatype=DataType.VARCHAR,
            enable_analyzer=True,
            analyzer_params={"type": "chinese"},
            max_length=65535,
        )
        # BM25 Function 自动生成稀疏向量
        schema.add_field(
            field_name="sparse",
            datatype=DataType.SPARSE_FLOAT_VECTOR,
        )
        schema.add_function(Function(
            name="memory_bm25_emb",
            function_type=FunctionType.BM25,
            input_field_names=["memory_card"],
            output_field_names=["sparse"],
        ))

        return schema

    def _build_index_params(self):
        """构建索引"""
        index_params = self.client.prepare_index_params()

        index_params.add_index(
            field_name="dense",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        index_params.add_index(
            field_name="sparse",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )

        return index_params

    def ensure_collection(self):
        """幂等连接"""
        if self._ready:
            return

        with self._lock:
            if self._ready:
                return

            if not self.client.has_collection(self.collection_name):
                try:
                    self.client.create_collection(
                        collection_name=self.collection_name,
                        schema=self._build_schema(),
                        index_params=self._build_index_params(),
                    )
                except MilvusException:
                    if not self.client.has_collection(self.collection_name):
                        raise

            self._ready = True
            self.client.load_collection(self.collection_name)

    def upsert(self, rows: list[dict]) -> dict:
        """写入或覆盖记忆。"""
        self.ensure_collection()
        return self.client.upsert(self.collection_name, rows)

    def list_memory_ids(
        self,
        user_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[str]:
        """查询 memory_id，可按用户/会话过滤。"""
        self.ensure_collection()

        filters = []
        if user_id is not None:
            filters.append(f"user_id == '{user_id}'")
        if thread_id is not None:
            filters.append(f"thread_id == '{thread_id}'")

        rows = self.client.query(
            collection_name=self.collection_name,
            filter=" and ".join(filters),
            output_fields=["memory_id"],
            limit=16384,
        )
        return [row["memory_id"] for row in rows]

    def delete(self, memory_ids: list[str]) -> dict:
        """按 memory_id 批量删除。"""
        if not memory_ids:
            return {"delete_count": 0}

        self.ensure_collection()

        total = 0
        for i in range(0, len(memory_ids), 100):
            result = self.client.delete(
                collection_name=self.collection_name,
                ids=memory_ids[i:i + 100],
            )
            total += result.get("delete_count", 0)

        return {"delete_count": total}

    def delete_by_thread(self, thread_id: str) -> dict:
        """删除某个会话的全部记忆。"""
        self.ensure_collection()
        return self.client.delete(
            collection_name=self.collection_name,
            filter=f"thread_id == '{thread_id}'",
        )

    def delete_by_user(self, user_id: str) -> dict:
        """删除某个用户的全部记忆。"""
        self.ensure_collection()
        return self.client.delete(
            collection_name=self.collection_name,
            filter=f"user_id == '{user_id}'",
        )

    def delete_all(self) -> dict:
        """删除全部记忆"""
        return self.delete(self.list_memory_ids())

    def search(
        self,
        query: str,
        query_vector: list[float],
        user_id: str,
        thread_id: str | None = None,
        top_k: int = 5,
    ) -> list[dict]:
        """混合检索 + RRF 融合。"""
        self.ensure_collection()

        expr = self._build_filter(user_id, thread_id)
        reqs = [
            AnnSearchRequest(
                data=[query_vector],
                anns_field="dense",
                param={"metric_type": "COSINE"},
                limit=top_k,
                expr=expr,
            ),
            AnnSearchRequest(
                data=[query],
                anns_field="sparse",
                param={"metric_type": "BM25"},
                limit=top_k,
                expr=expr,
            ),
        ]

        hits = self.client.hybrid_search(
            collection_name=self.collection_name,
            reqs=reqs,
            ranker=RRFRanker(60),
            limit=top_k,
            output_fields=self._output_fields(),
        )

        return [
            self._to_memory(hit)
            for hit in (hits[0] if hits else [])
        ]

    @staticmethod
    def _build_filter(user_id: str, thread_id: str | None) -> str:
        filters = [f"user_id == '{user_id}'"]
        if thread_id is not None:
            filters.append(
                f"thread_id == '{thread_id}'"
            )
        return " and ".join(filters)

    @staticmethod
    def _output_fields() -> list[str]:
        return [
            "memory_id", "thread_id", "user_id",
            "content", "created_at", "search_text"
        ]

    @staticmethod
    def _to_memory(hit: dict) -> dict:
        entity = hit.get("entity", {})
        return {
            "memory_id": entity.get("memory_id", ""),
            "thread_id": entity.get("thread_id", ""),
            "user_id": entity.get("user_id", ""),
            "content": entity.get("content", ""),
            "created_at": int(entity.get("created_at") or 0),
            "search_text": entity.get("search_text", ""),
            "score": float(hit.get("distance", 0.0)),
        }

    async def close(self) -> None:
        self.client.close()
        self._ready = False

@cache
def get_memory_store() -> MemoryStore:
    """全进程复用一个 Store"""
    return MemoryStore(collection_name="multi_agent")

async def close_memory_store() -> None:
    """关闭记忆库连接并清除工厂缓存。"""
    if get_memory_store.cache_info().currsize:
        await get_memory_store().close()

    get_memory_store.cache_clear()

