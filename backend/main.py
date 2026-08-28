"""
Бэкенд игры «Зефирные космолёты».
FastAPI + WebSockets + SQLite/PostgreSQL (SQLAlchemy).
Запуск: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
import random
import string
import sys
from pathlib import Path
import os

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

# Добавляем папку backend в путь поиска модулей
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ---- Проверка импортов ----
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

# ---- Стартовая инициализация ----
@app.on_event("startup")
def startup():
    try:
        models.Base.metadata.create_all(bind=engine)
        print("✅ Таблицы созданы (или уже существуют)")
    except Exception as e:
        print(f"⚠️ Ошибка создания таблиц: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PHASE_ORDER = ["lobby", "challenge", "upgrade", "battle", "finished"]

# ---------------------------------------------------------------- utils ----

def gen_room_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=5))

def get_game(db: Session, room_code: str) -> m.Game:
    game = db.query(m.Game).filter(m.Game.room_code == room_code.upper()).first()
    if not game:
        raise HTTPException(404, "Игра с таким кодом не найдена")
    return game

def require_admin(db: Session, room_code: str, token: str) -> m.Game:
    game = get_game(db, room_code)
    if token != game.admin_token:
        raise HTTPException(403, "Неверный токен ведущего")
    return game

def require_team(db: Session, room_code: str, token: str) -> tuple[m.Game, m.Team]:
    game = get_game(db, room_code)
    team = db.query(m.Team).filter(
        m.Team.game_id == game.id, m.Team.token == token
    ).first()
    if not team:
        raise HTTPException(403, "Неверный токен команды")
    return game, team

def log(db: Session, game: m.Game, text: str):
    db.add(m.HistoryEntry(game_id=game.id, round_number=game.round_number, text=text))

# ------------------------------------------------------------ serializers --

def team_actions_used_this_round(game: m.Game, team_id: str) -> list:
    return [a for a in game.actions if a.team_id == team_id and a.round_number == game.round_number]

def team_battle_actions_left(team: m.Team, game: m.Game) -> int:
    used = team_actions_used_this_round(game, team.id)
    used_slots = sum(1 for a in used if a.action_type in ("attack", "repair"))
    base_slots = 1 + gl.energy_extra_actions(team)
    if team.double_action_charges > 0:
        base_slots += 1
    return max(0, base_slots - used_slots)

def team_full_dict(t: m.Team) -> dict:
    return {
        "id": t.id, "name": t.name, "coins": t.coins,
        "attack": t.lvl_attack, "defense": t.lvl_defense,
        "energy": t.lvl_energy, "tactics": t.lvl_tactics,
        "rating": gl.rating(t), "connected": t.connected,
        "shield_active": t.shield_active,
        "bought_shield": t.bought_shield,
        "bought_mass_attack": t.bought_mass_attack,
        "bought_theft": t.bought_theft,
        "bought_boost": t.bought_boost,
        "bought_double": t.bought_double,
        "free_upgrade_charges": t.free_upgrade_charges,
        "double_action_charges": t.double_action_charges,
    }

def team_filtered_dict(t: m.Team, revealed: bool) -> dict:
    d = {
        "id": t.id, "name": t.name, "rating": gl.rating(t), "revealed": revealed,
        "shield_active": t.shield_active,
    }
    fields = gl.visible_fields(0 if revealed else t.lvl_defense)
    if "coins" in fields:
        d["coins"] = t.coins
    if "attack" in fields:
        d["attack"] = t.lvl_attack
    if "defense" in fields:
        d["defense"] = t.lvl_defense
    if "energy" in fields:
        d["energy"] = t.lvl_energy
    if "tactics" in fields:
        d["tactics"] = t.lvl_tactics
    return d

def build_view(db: Session, game: m.Game, role: str, team_id: str | None) -> dict:
    base = {
        "type": "state",
        "room_code": game.room_code,
        "phase": game.phase,
        "round_number": game.round_number,
        "total_rounds": game.total_rounds,
        "history": [h.text for h in sorted(game.history, key=lambda x: x.created_at)][-50:],
    }
    if role == "admin":
        base["teams"] = [team_full_dict(t) for t in game.teams]
        base["actions"] = [
            {
                "id": a.id, "team_id": a.team_id,
                "team_name": next((t.name for t in game.teams if t.id == a.team_id), "?"),
                "action_type": a.action_type, "target_team_id": a.target_team_id,
                "target_name": next((t.name for t in game.teams if t.id == a.target_team_id), None),
                "module": a.module, "cost": a.cost, "status": a.status,
                "result_text": a.result_text,
            }
            for a in sorted(game.actions, key=lambda x: x.created_at, reverse=True)
            if a.round_number == game.round_number
        ]
        base["admin_token"] = game.admin_token
        return base

    me = next((t for t in game.teams if t.id == team_id), None)
    base["me"] = team_full_dict(me) if me else None
    revealed = gl.scouted_ids(me) if me else set()
    base["board"] = [
        team_full_dict(t) if t.id == team_id else team_filtered_dict(t, t.id in revealed)
        for t in game.teams
    ]
    if me:
        my_actions = [a for a in game.actions if a.team_id == me.id and a.round_number == game.round_number]
        base["my_actions"] = [
            {
                "id": a.id, "action_type": a.action_type,
                "target_team_id": a.target_team_id, "module": a.module,
                "cost": a.cost, "status": a.status, "result_text": a.result_text,
            }
            for a in sorted(my_actions, key=lambda x: x.created_at, reverse=True)
        ]
        base["upgrade_costs"] = {
            mod: gl.upgrade_cost(getattr(me, f"lvl_{mod}")) for mod in gl.MODULES
        }
        base["damaged_modules"] = gl.damaged_modules(me)
        actions_left = team_battle_actions_left(me, game)
        base["can_attack"] = (
            game.phase == "battle" and actions_left > 0
            and any(t.id != me.id for t in game.teams)
        )
        base["can_repair"] = (
            game.phase == "battle" and actions_left > 0
            and len(gl.damaged_modules(me)) > 0
        )
        base["can_scout"] = (
            game.phase == "battle"
            and any(t.id != me.id and t.id not in revealed for t in game.teams)
        )
        base["can_choose_attack_module"] = gl.can_choose_attack_module(me)
        base["actions_left"] = actions_left
        base["service_prices"] = {s: gl.service_cost(me, s) for s in gl.SERVICE_PRICES}
    return base

async def push(db: Session, game: m.Game):
    db.commit()
    db.refresh(game)
    def builder(role, team_id):
        return build_view(db, game, role, team_id)
    await manager.broadcast_state(game.room_code, builder)

# ------------------------------------------------------------- schemas ----

class CreateGameIn(BaseModel):
    total_rounds: int = 5
    max_teams: int = 6

class JoinIn(BaseModel):
    name: str

class ScoreIn(BaseModel):
    token: str
    scores: dict[str, int]

class PhaseIn(BaseModel):
    token: str

class UpgradeIn(BaseModel):
    token: str
    module: str

class TeamActionIn(BaseModel):
    token: str
    action_type: str
    target_team_id: str | None = None
    module: str | None = None

class BuyServiceIn(BaseModel):
    token: str
    service: str

# ------------------------------------------------------------- endpoints (сокращённо для диагностики) ----

@app.get("/ping")
def ping():
    return {"status": "ok", "message": "Server is alive!"}

@app.get("/")
def root():
    try:
        return HTMLResponse(content="<h1>Зефирные космолёты</h1><p>Сервер работает. Файлы фронтенда будут позже.</p>")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# Остальные эндпоинты пока можно закомментировать для теста,
# но оставим только базовые, чтобы проверить работоспособность.

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
    print("⚠️ FRONTEND_DIR not found")
