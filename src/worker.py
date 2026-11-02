from rq.worker import SpawnWorker
from rq import Queue
from redis import Redis
import dotenv
import os

dotenv.load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INGESTION_QUEUE_NAME = os.getenv("INGESTION_QUEUE_NAME", "ingestion")

redis_conn = Redis.from_url(REDIS_URL)
queue = Queue(INGESTION_QUEUE_NAME, connection=redis_conn)

if __name__ == "__main__":
    worker = SpawnWorker(
        [queue],
        connection=redis_conn
    )

    worker.work()
