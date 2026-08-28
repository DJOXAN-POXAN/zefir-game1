"""
Бэкенд игры «Зефирные космолёты».
FastAPI + WebSockets + SQLite/PostgreSQL (SQLAlchemy).
Запуск: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
import random
import string
import sys
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

# Добавляем папку backend в путь поиска модулей
sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import Base, engine, get_db
import models as m
import game_logic as gl
from ws_manager import manager

app = FastAPI(title="Зефирные космолёты")


@app.on_event("startup")
def startup():
    try:
        import models
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
    """
    Сколько "боевых" действий (атака или восстановление — общий пул, каждое
    доступно не более одного раза за раунд; разведка в этот пул не входит
    и не лимитирована) ещё доступно команде в этой битве.
    Базово — 1 действие за раунд. Энергия 3+ даёт +1. Купленный "Двойной ход"
    даёт ещё +1, но только один раз за всю игру (заряд сгорает по итогу раунда).
    """
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

    # role == "team"
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
        # Возможности в фазе битвы — фронт использует их, чтобы дизейблить кнопки.
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
    scores: dict[str, int]  # team_id -> points


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


# ------------------------------------------------------------- endpoints --

@app.post("/api/games/{room_code}/team/buy_service")
async def buy_service(room_code: str, body: BuyServiceIn, db: Session = Depends(get_db)):
    game, team = require_team(db, room_code, body.token)
    if game.phase != "upgrade":
        raise HTTPException(400, "Покупка доступна только в фазе Техобслуживания")

    service = body.service
    if service not in gl.SERVICE_PRICES:
        raise HTTPException(400, "Неизвестная услуга")
    if gl.has_bought(team, service):
        raise HTTPException(400, "Эта услуга уже куплена (доступна один раз за игру)")

    cost = gl.service_cost(team, service)
    if team.coins < cost:
        raise HTTPException(400, "Недостаточно монет")

    team.coins -= cost
    setattr(team, f"bought_{service}", True)

    if service == "shield":
        team.shield_active = True
        log(db, game, f"«{team.name}» купил(а) щит! 🛡️")
    elif service == "mass_attack":
        dmg = max(1, team.lvl_attack // 2)
        hit_names = []
        for target in game.teams:
            if target.id == team.id:
                continue
            if target.shield_active:
                target.shield_active = False
                hit_names.append(f"{target.name} (щит поглотил удар)")
                continue
            mods = [mod for mod in gl.MODULES if getattr(target, f"lvl_{mod}") > 1]
            if mods:
                chosen = random.choice(mods)
                gl.apply_damage(target, chosen, 1)
                hit_names.append(target.name)
        log(db, game, f"«{team.name}» провёл(а) массовую атаку (урон {dmg})! Задеты: {', '.join(hit_names) if hit_names else 'никто'}.")
    elif service == "theft":
        targets = [t for t in game.teams if t.id != team.id and t.coins > 0]
        if not targets:
            # Откатываем списание — украсть не у кого, но услуга всё равно расходуется по правилам.
            # Чтобы не наказывать команду за невезение, не списываем монеты и не отмечаем как купленную.
            team.coins += cost
            setattr(team, f"bought_{service}", False)
            raise HTTPException(400, "Некого грабить (у всех 0 монет), услуга не потрачена")
        target = random.choice(targets)
        stolen = min(30, target.coins)
        target.coins -= stolen
        team.coins += stolen
        log(db, game, f"«{team.name}» украл(а) {stolen} монет у «{target.name}» 💰")
    elif service == "boost":
        team.free_upgrade_charges += 1
        log(db, game, f"«{team.name}» купил(а) ускорение — следующее улучшение бесплатно ⚡")
    elif service == "double":
        team.double_action_charges += 1
        log(db, game, f"«{team.name}» купил(а) двойной ход — доп. действие в следующей битве 🌀")

    await push(db, game)
    return {"ok": True, "message": f"Услуга «{gl.SERVICE_NAMES[service]}» применена"}


@app.post("/api/games")
def create_game(body: CreateGameIn, db: Session = Depends(get_db)):
    code = gen_room_code()
    while db.query(m.Game).filter(m.Game.room_code == code).first():
        code = gen_room_code()
    game = m.Game(
        room_code=code, total_rounds=body.total_rounds, max_teams=body.max_teams
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    return {"room_code": game.room_code, "admin_token": game.admin_token}


@app.post("/api/games/{room_code}/join")
async def join_game(room_code: str, body: JoinIn, db: Session = Depends(get_db)):
    game = get_game(db, room_code)
    if game.phase != "lobby":
        raise HTTPException(400, "Игра уже началась, присоединиться нельзя")
    if len(game.teams) >= game.max_teams:
        raise HTTPException(400, "Все места заняты")
    name = body.name.strip()[:30]
    if not name:
        raise HTTPException(400, "Введите название команды")
    if any(t.name.lower() == name.lower() for t in game.teams):
        raise HTTPException(400, "Команда с таким названием уже есть")
    team = m.Team(game_id=game.id, name=name)
    db.add(team)
    log(db, game, f"Команда «{name}» присоединилась к игре.")
    await push(db, game)
    return {"team_id": team.id, "token": team.token, "room_code": game.room_code}


@app.get("/api/games/{room_code}/state")
def get_state(room_code: str, token: str, role: str, db: Session = Depends(get_db)):
    game = get_game(db, room_code)
    if role == "admin":
        require_admin(db, room_code, token)
        return build_view(db, game, "admin", None)
    _, team = require_team(db, room_code, token)
    return build_view(db, game, "team", team.id)


@app.post("/api/games/{room_code}/admin/start")
async def admin_start(room_code: str, body: PhaseIn, db: Session = Depends(get_db)):
    game = require_admin(db, room_code, body.token)
    if game.phase != "lobby":
        raise HTTPException(400, "Игра уже запущена")
    if not game.teams:
        raise HTTPException(400, "Нет ни одной команды")
    game.phase = "challenge"
    game.round_number = 1
    log(db, game, f"Игра началась. Раунд {game.round_number}: «Космический вызов».")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/admin/score")
async def admin_score(room_code: str, body: ScoreIn, db: Session = Depends(get_db)):
    game = require_admin(db, room_code, body.token)
    if game.phase != "challenge":
        raise HTTPException(400, "Начисление очков доступно только в фазе «Космический вызов»")
    for team_id, points in body.scores.items():
        team = next((t for t in game.teams if t.id == team_id), None)
        if not team:
            continue
        points = max(0, int(points))
        team.coins += points
        log(db, game, f"«{team.name}» получает {points} монет за раунд {game.round_number}.")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/admin/next_phase")
async def admin_next_phase(room_code: str, body: PhaseIn, db: Session = Depends(get_db)):
    game = require_admin(db, room_code, body.token)
    if game.phase == "challenge":
        game.phase = "upgrade"
        log(db, game, "Фаза «Техобслуживание»: команды прокачивают корабли.")
    elif game.phase == "upgrade":
        game.phase = "battle"
        log(db, game, "Фаза «Звёздная битва»: команды выбирают действия.")
    elif game.phase == "battle":
        # Заряд "Двойного хода" расходуется по итогам той битвы, для которой был куплен,
        # использован он был или нет.
        for t in game.teams:
            if t.double_action_charges > 0:
                t.double_action_charges = 0
        if game.round_number >= game.total_rounds:
            game.phase = "finished"
            log(db, game, "Игра завершена!")
        else:
            game.round_number += 1
            game.phase = "challenge"
            for t in game.teams:
                t.scouted_targets = ""
                bonus = gl.energy_round_income(t)
                if bonus:
                    t.coins += bonus
                    log(db, game, f"«{t.name}» получает {bonus} бонусных монет от Энергии 5. ⚡")
            log(db, game, f"Раунд {game.round_number}: «Космический вызов».")
    else:
        raise HTTPException(400, "Нет следующей фазы")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/admin/reset")
async def admin_reset(room_code: str, body: PhaseIn, db: Session = Depends(get_db)):
    game = require_admin(db, room_code, body.token)
    for t in game.teams:
        t.coins = 0
        t.lvl_attack = t.lvl_defense = t.lvl_energy = t.lvl_tactics = 1
        t.dmg_attack = t.dmg_defense = t.dmg_energy = t.dmg_tactics = 0
        t.scouted_targets = ""
        t.bought_shield = t.bought_mass_attack = t.bought_theft = False
        t.bought_boost = t.bought_double = False
        t.shield_active = False
        t.free_upgrade_charges = 0
        t.double_action_charges = 0
    db.query(m.Action).filter(m.Action.game_id == game.id).delete()
    db.query(m.HistoryEntry).filter(m.HistoryEntry.game_id == game.id).delete()
    game.phase = "lobby"
    game.round_number = 0
    log(db, game, "Игра сброшена ведущим.")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/team/upgrade")
async def team_upgrade(room_code: str, body: UpgradeIn, db: Session = Depends(get_db)):
    game, team = require_team(db, room_code, body.token)
    if game.phase != "upgrade":
        raise HTTPException(400, "Прокачка доступна только в фазе «Техобслуживание»")
    if body.module not in gl.MODULES:
        raise HTTPException(400, "Неизвестный модуль")
    current = getattr(team, f"lvl_{body.module}")
    cost = gl.upgrade_cost(current)
    if cost is None:
        raise HTTPException(400, "Модуль уже максимального уровня")

    used_free = False
    if team.free_upgrade_charges > 0:
        used_free = True
    elif team.coins < cost:
        raise HTTPException(400, "Недостаточно монет")

    if used_free:
        team.free_upgrade_charges -= 1
    else:
        team.coins -= cost

    setattr(team, f"lvl_{body.module}", current + 1)
    suffix = " (бесплатно, ускорение ⚡)" if used_free else ""
    log(db, game, f"«{team.name}» улучшает модуль «{gl.MODULE_NAMES[body.module]}» до уровня {current + 1}{suffix}.")
    await push(db, game)
    return {"ok": True}


@app.post("/api/games/{room_code}/team/action")
async def team_action(room_code: str, body: TeamActionIn, db: Session = Depends(get_db)):
    """
    Действия применяются НЕМЕДЛЕННО, без подтверждения ведущим — чтобы
    несколько команд могли играть параллельно без ожидания в очереди.
    """
    game, team = require_team(db, room_code, body.token)
    if game.phase != "battle":
        raise HTTPException(400, "Действия доступны только в фазе «Звёздная битва»")

    this_round_actions = team_actions_used_this_round(game, team.id)

    if body.action_type == "attack":
        if team_battle_actions_left(team, game) <= 0:
            raise HTTPException(400, "Лимит действий в этом раунде исчерпан")
        target = next((t for t in game.teams if t.id == body.target_team_id), None)
        if not target or target.id == team.id:
            raise HTTPException(400, "Выберите корректную цель")
        cost = gl.ACTION_BASE_COST["attack"]
        if team.coins < cost:
            raise HTTPException(400, "Недостаточно монет для этого действия")

        chosen_module = body.module if gl.can_choose_attack_module(team) else None
        if chosen_module and chosen_module not in gl.MODULES:
            raise HTTPException(400, "Неизвестный модуль для атаки")

        team.coins -= cost
        has_shield = target.shield_active
        damage, hit_module, text = gl.resolve_attack(team, target, chosen_module, has_shield)
        if has_shield:
            target.shield_active = False

        action = m.Action(
            game_id=game.id, round_number=game.round_number, team_id=team.id,
            action_type="attack", target_team_id=target.id, module=hit_module, cost=cost,
            result_text=text,
        )
        db.add(action)
        log(db, game, text)

    elif body.action_type == "scout":
        target = next((t for t in game.teams if t.id == body.target_team_id), None)
        if not target or target.id == team.id:
            raise HTTPException(400, "Выберите корректную цель")
        current = gl.scouted_ids(team)
        if target.id in current:
            raise HTTPException(400, "Эта команда уже разведана в этом раунде")
        current.add(target.id)
        team.scouted_targets = ",".join(current)
        text = f"«{team.name}» провёл(а) разведку «{target.name}» — корабль раскрыт до конца раунда."
        action = m.Action(
            game_id=game.id, round_number=game.round_number, team_id=team.id,
            action_type="scout", target_team_id=target.id, cost=0, result_text=text,
        )
        db.add(action)
        log(db, game, text)

    elif body.action_type == "repair":
        if team_battle_actions_left(team, game) <= 0:
            raise HTTPException(400, "Лимит действий в этом раунде исчерпан")
        if body.module not in gl.MODULES:
            raise HTTPException(400, "Выберите модуль для восстановления")
        if getattr(team, f"dmg_{body.module}") <= 0:
            raise HTTPException(400, "Этот модуль не повреждён — восстанавливать нечего")
        cost = gl.ACTION_BASE_COST["repair"]
        if team.coins < cost:
            raise HTTPException(400, "Недостаточно монет для этого действия")

        team.coins -= cost
        new_level = gl.repair_module(team, body.module)
        text = f"«{team.name}» восстановил(а) модуль «{gl.MODULE_NAMES[body.module]}» до уровня {new_level}."
        action = m.Action(
            game_id=game.id, round_number=game.round_number, team_id=team.id,
            action_type="repair", module=body.module, cost=cost, result_text=text,
        )
        db.add(action)
        log(db, game, text)

    else:
        raise HTTPException(400, "Неизвестное действие")

    await push(db, game)
    return {"ok": True}


# ---------------------------------------------------------------- websocket

@app.websocket("/ws/{room_code}")
async def ws_endpoint(websocket: WebSocket, room_code: str, token: str, role: str):
    db = next(get_db())
    try:
        game = get_game(db, room_code)
    except HTTPException:
        await websocket.close(code=4404)
        return

    team_id = None
    if role == "admin":
        if token != game.admin_token:
            await websocket.close(code=4403)
            return
    else:
        team = db.query(m.Team).filter(m.Team.game_id == game.id, m.Team.token == token).first()
        if not team:
            await websocket.close(code=4403)
            return
        team_id = team.id
        team.connected = True
        db.commit()

    await manager.connect(room_code, websocket, role, team_id)
    await websocket.send_json(build_view(db, game, role, team_id))
    try:
        while True:
            await websocket.receive_text()  # клиент может присылать ping
    except WebSocketDisconnect:
        manager.disconnect(room_code, websocket)
        if role == "team" and team_id:
            team = db.query(m.Team).filter(m.Team.id == team_id).first()
            if team:
                team.connected = False
                db.commit()


# -------------------------------------------------------- статика фронтенда

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/admin")
def admin_page():
    return FileResponse(str(FRONTEND_DIR / "admin.html"))


@app.get("/team")
def team_page():
    return FileResponse(str(FRONTEND_DIR / "team.html"))
