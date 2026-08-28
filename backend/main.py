"""
Бэкенд игры «Зефирные космолёты».
Минимальная версия для диагностики.
"""
import random
import string
import sys
from pathlib import Path
import os

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

# Добавляем папку backend в путь поиска модулей
sys.path.insert(0, str(Path(__file__).resolve().parent))

print("🔍 Проверка импортов...")
try:
    from database import Base, engine, get_db
    print("✅ database импортирован")
except Exception as e:
    print(f"❌ Ошибка импорта database: {e}")
    raise

try:
    import models as m
    print("✅ models импортирован")
except Exception as e:
    print(f"❌ Ошибка импорта models: {e}")
    raise

try:
    import game_logic as gl
    print("✅ game_logic импортирован")
except Exception as e:
    print(f"❌ Ошибка импорта game_logic: {e}")
    raise

try:
    from ws_manager import manager
    print("✅ ws_manager импортирован")
except Exception as e:
    print(f"❌ Ошибка импорта ws_manager: {e}")
    raise

print("✅ Все импорты успешны")

app = FastAPI(title="Зефирные космолёты")

@app.on_event("startup")
def startup():
    try:
        m.Base.metadata.create_all(bind=engine)
        print("✅ Таблицы созданы (или уже существуют)")
    except Exception as e:
        print(f"⚠️ Ошибка создания таблиц: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Тестовые эндпоинты ----
@app.get("/ping")
def ping():
    return {"status": "ok", "message": "Server is alive!"}

@app.get("/")
def root():
    return HTMLResponse(content="<h1>Зефирные космолёты</h1><p>Сервер работает. Файлы фронтенда будут позже.</p>")

# -------------------------------------------------------- статика фронтенда
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
print(f"📁 BASE_DIR = {BASE_DIR}")
print(f"📁 FRONTEND_DIR = {FRONTEND_DIR}")
print(f"📁 FRONTEND_DIR exists: {FRONTEND_DIR.exists()}")
if FRONTEND_DIR.exists():
    print(f"📄 Files: {[f.name for f in FRONTEND_DIR.iterdir()]}")
    try:
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
        print("✅ Static mounted")
    except Exception as e:
        print(f"❌ Static mount error: {e}")
else:
    print("⚠️ FRONTEND_DIR не найдена")

# Можно добавить остальные эндпоинты позже, когда убедимся, что сервер стабилен.
