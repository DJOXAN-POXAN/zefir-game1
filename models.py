"""
ORM-модели игры «Зефирные космолёты».
"""
import uuid
import datetime as dt

from sqlalchemy import (
    Column, String, Integer, Boolean, ForeignKey, DateTime, Text
)
from sqlalchemy.orm import relationship

from database import Base


def gen_id() -> str:
    return uuid.uuid4().hex[:12]


class Game(Base):
    __tablename__ = "games"

    id = Column(String, primary_key=True, default=gen_id)
    room_code = Column(String, unique=True, index=True, nullable=False)
    admin_token = Column(String, unique=True, nullable=False, default=gen_id)

    # phase: "lobby" | "challenge" | "upgrade" | "battle" | "finished"
    phase = Column(String, default="lobby")
    round_number = Column(Integer, default=0)
    total_rounds = Column(Integer, default=5)

    max_teams = Column(Integer, default=6)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    teams = relationship(
        "Team", back_populates="game", cascade="all, delete-orphan"
    )
    actions = relationship(
        "Action", back_populates="game", cascade="all, delete-orphan"
    )
    history = relationship(
        "HistoryEntry", back_populates="game", cascade="all, delete-orphan"
    )


class Team(Base):
    __tablename__ = "teams"

    id = Column(String, primary_key=True, default=gen_id)
    game_id = Column(String, ForeignKey("games.id"))
    token = Column(String, unique=True, nullable=False, default=gen_id)

    name = Column(String, nullable=False)
    coins = Column(Integer, default=0)

    # 4 модуля корабля
    lvl_attack = Column(Integer, default=1)
    lvl_defense = Column(Integer, default=1)
    lvl_energy = Column(Integer, default=1)
    lvl_tactics = Column(Integer, default=1)

    # На сколько уровней сейчас "просажен" модуль относительно того,
    # что команда сама прокачала (используется, чтобы repair
    # не поднимал модуль выше исходного уровня и не чинил то, что не сломано).
    dmg_attack = Column(Integer, default=0)
    dmg_defense = Column(Integer, default=0)
    dmg_energy = Column(Integer, default=0)
    dmg_tactics = Column(Integer, default=0)

    # Разовые услуги: каждая может быть куплена только один раз за игру.
    # bought_* — услуга куплена (для иконок и блокировки повторной покупки).
    bought_shield = Column(Boolean, default=False)
    bought_mass_attack = Column(Boolean, default=False)
    bought_theft = Column(Boolean, default=False)
    bought_boost = Column(Boolean, default=False)
    bought_double = Column(Boolean, default=False)

    shield_active = Column(Boolean, default=False)        # щит ждёт атаки
    free_upgrade_charges = Column(Integer, default=0)      # заряды бесплатного улучшения
    double_action_charges = Column(Integer, default=0)     # заряды доп. действия в битве

    # разведка: до конца раунда действует "снятая завеса" над указанными целями
    scouted_targets = Column(Text, default="")  # comma-separated team ids

    connected = Column(Boolean, default=False)
    joined_at = Column(DateTime, default=dt.datetime.utcnow)

    game = relationship("Game", back_populates="teams")


class Action(Base):
    """
    Действие команды в фазе битвы. Применяется сразу при создании
    (без подтверждения ведущим) — хранится как факт для истории/лога.
    """
    __tablename__ = "actions"

    id = Column(String, primary_key=True, default=gen_id)
    game_id = Column(String, ForeignKey("games.id"))
    round_number = Column(Integer, nullable=False)

    team_id = Column(String, ForeignKey("teams.id"))
    action_type = Column(String, nullable=False)  # attack | scout | repair
    target_team_id = Column(String, nullable=True)
    module = Column(String, nullable=True)  # для repair: какой модуль чинить
    cost = Column(Integer, default=0)

    # status всегда "applied" — оставлено для совместимости отображения
    status = Column(String, default="applied")
    result_text = Column(Text, default="")

    created_at = Column(DateTime, default=dt.datetime.utcnow)

    game = relationship("Game", back_populates="actions")


class HistoryEntry(Base):
    __tablename__ = "history"

    id = Column(String, primary_key=True, default=gen_id)
    game_id = Column(String, ForeignKey("games.id"))
    round_number = Column(Integer, default=0)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)

    game = relationship("Game", back_populates="history")
