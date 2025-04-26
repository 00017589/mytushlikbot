import asyncio
from database import users_col

async def clear_users():
    res = await users_col.delete_many({})
    print(f"✅ Deleted {res.deleted_count} users.")

if __name__ == "__main__":
    asyncio.run(clear_users())
