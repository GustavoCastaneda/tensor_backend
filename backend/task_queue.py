# backend/task_queue.py
import os
import redis
from rq import Queue
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_conn = redis.from_url(redis_url, decode_responses=True, ssl_cert_reqs=None)

# Creamos una cola llamada 'ingest'
ingest_queue = Queue("ingest", connection=redis_conn)