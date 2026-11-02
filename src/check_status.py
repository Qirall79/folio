from redis import Redis
from rq.job import Job

redis_conn = Redis.from_url("redis://localhost:6379/0")

job = Job.fetch("ae716a5d-fcb4-4286-a478-4e983b15f239", connection=redis_conn)

print(job.get_status(refresh=True))
print(job.result)