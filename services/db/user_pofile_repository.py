from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

class UserProfileRepository:
    """用户长期画像数据访问层"""
    def __init__(self, pool):
        self.pool = pool

    async def get(self, user_id: str) -> dict | None:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    select profile
                    from user_profiles
                    where user_id = %s
                    """,
                    (user_id,),
                )
                row = await cur.fetchone()
                return row['profile'] if row else None

    async def upsert(self, user_id: str, profile: dict) -> None:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into user_profiles (
                        user_id, profile
                    )
                    values (%s, %s)
                    on conflict (user_id) do update
                    set
                        profile = excluded.profile,
                        updated_at = now()
                    """,
                    (user_id, Jsonb(profile)),
                )