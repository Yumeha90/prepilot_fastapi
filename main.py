from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello from prepilot FastAPI!"}

@app.get("/health")
def health():
    return {"status": "ok"}

# --- Celery 最小配置 ---
from celery import Celery

celery_app = Celery(
    "app",
    broker="amqp://guest:guest@rabbitmq:5672//",
    backend="rpc://",
)

@celery_app.task
def add(x, y):
    return x + y
