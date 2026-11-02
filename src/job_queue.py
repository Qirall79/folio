import os
from redis import Redis
from rq import Queue
import dotenv

dotenv.load_dotenv()


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INGESTION_QUEUE_NAME = os.getenv("INGESTION_QUEUE_NAME", "ingestion")

redis_conn = Redis.from_url(REDIS_URL)

queue = Queue(INGESTION_QUEUE_NAME, connection=redis_conn)