import random

MODULES = ["attack", "defense", "energy", "tactics"]
MODULE_NAMES = {"attack": "Атака", "defense": "Защита", "energy": "Энергия", "tactics": "Тактика"}
MAX_LEVEL = 5

ACTION_BASE_COST = {"attack": 30, "scout": 0, "repair": 40}

UPGRADE_COST_TABLE = {1: 50, 2: 150, 3: 300, 4: 500}

SERVICE_PRICES = {"shield": 50, "mass_attack": 80, "theft": 60, "boost": 40, "double": 70}
SERVICE_NAMES = {
    "shield": "Щит", "mass_attack": "Массовая атака", "theft": "Кража",
    "boost": "Ускорение", "double": "Двойной ход",
}


def rating(team) -> int:
    """Общий рейтинг = сумма уровней всех модулей."""
    return team.lvl_attack + team.lvl_defense + team.lvl_energy + team.lvl_tactics


def visible_fields(defense_level: int) -> list:
    """
    Какие поля видны другим командам в зависимости от уровня Защиты цели.
    Чем выше уровень Защиты, тем больше скрыто (скрытность — побочный эффект Защиты).
    Защита 1: видно всё. Защита 2+: скрыты монеты. Защита 3+: скрыта атака.
    Защита 4+: скрыта энергия. Защита 5+: скрыта тактика.
    """
    base = ["name", "rating", "defense"]
    if defense_level < 2:
        base.append("coins")
    if defense_level < 3:
        base.append("attack")
    if defense_level < 4:
        base.append("energy")
    if defense_level < 5:
        base.append("tactics")
    return base


def upgrade_cost(current_level: int):
    """Стоимость улучшения с current_level на current_level+1. None если уже макс."""
    return UPGRADE_COST_TABLE.get(current_level)  # если уровень 5 — вернёт None


def service_cost(team, service: str) -> int:
    """Стоимость услуги с учётом скидки 20% от Тактики 5+."""
    cost = SERVICE_PRICES[service]
    if team.lvl_tactics >= 5:
        cost = int(cost * 0.8)
    return cost


def has_bought(team, service: str) -> bool:
    return bool(getattr(team, f"bought_{service}"))


def energy_extra_actions(team) -> int:
    """Сколько ДОПОЛНИТЕЛЬНЫХ действий даёт модуль Энергия за раунд (сверх одного базового)."""
    return 1 if team.lvl_energy >= 3 else 0


def energy_round_income(team) -> int:
    """Бонусные монеты в начале раунда от Энергии 5+."""
    return 10 if team.lvl_energy >= 5 else 0


def can_choose_attack_module(team) -> bool:
    """Тактика 3+ позволяет выбрать модуль цели при атаке вместо случайного."""
    return team.lvl_tactics >= 3


def scouted_ids(team) -> set:
    return set(x for x in (team.scouted_targets or "").split(",") if x)


def damaged_modules(team) -> list:
    """Список модулей, которые сейчас повреждены (dmg_* > 0) и подлежат восстановлению."""
    return [m for m in MODULES if getattr(team, f"dmg_{m}") > 0]


def apply_damage(team, module: str, amount: int = 1):
    """Понижает уровень модуля на amount (не ниже 1) и запоминает урон для repair."""
    current = getattr(team, f"lvl_{module}")
    new_level = max(1, current - amount)
    actual = current - new_level
    if actual > 0:
        setattr(team, f"lvl_{module}", new_level)
        dmg_field = f"dmg_{module}"
        setattr(team, dmg_field, getattr(team, dmg_field) + actual)
    return actual


def repair_module(team, module: str) -> int:
    """
    Восстанавливает модуль ровно на 1 уровень урона, не выше исходного
    (то есть не выше lvl + dmg, где dmg ещё остался).
    Возвращает новый уровень.
    """
    dmg_field = f"dmg_{module}"
    dmg = getattr(team, dmg_field)
    if dmg <= 0:
        return getattr(team, f"lvl_{module}")
    lvl_field = f"lvl_{module}"
    new_level = min(MAX_LEVEL, getattr(team, lvl_field) + 1)
    setattr(team, lvl_field, new_level)
    setattr(team, dmg_field, dmg - 1)
    return new_level


def resolve_attack(attacker, target, chosen_module: str | None, target_has_shield: bool = False):
    """
    Возвращает (нанесённый_урон, имя_повреждённого_модуля, сообщение).
    Если у цели активен щит — урон полностью блокируется, щит снимается, дальше не идём.
    chosen_module задаётся, если у атакующего Тактика 3+ (выбор модуля цели),
    иначе выбирается случайный модуль цели с уровнем > 1.
    """
    if target_has_shield:
        return 0, None, f"«{attacker.name}» атакует «{target.name}», но щит полностью блокирует урон!"

    dmg = max(0, attacker.lvl_attack - target.lvl_defense)
    if dmg <= 0:
        return 0, None, f"Атака «{attacker.name}» не нанесла урона (защита «{target.name}» поглотила всё)."

    mods = [m for m in MODULES if getattr(target, f"lvl_{m}") > 1]
    if not mods:
        return dmg, None, f"Урон {dmg}, но все модули «{target.name}» уже на минимуме."

    if chosen_module and chosen_module in mods:
        chosen = chosen_module
    else:
        chosen = random.choice(mods)

    apply_damage(target, chosen, 1)
    new_lvl = getattr(target, f"lvl_{chosen}")
    return dmg, chosen, (
        f"«{attacker.name}» наносит {dmg} урона «{target.name}» "
        f"и снижает модуль «{MODULE_NAMES[chosen]}» до {new_lvl}."
    )
