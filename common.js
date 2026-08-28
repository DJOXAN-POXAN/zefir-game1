// Общие утилиты для страниц ведущего и команды.

const API_BASE = "";

const MODULE_META = {
  attack:  { label: "Атака",    cls: "attack" },
  defense: { label: "Защита",   cls: "defense" },
  energy:  { label: "Энергия",  cls: "energy" },
  tactics: { label: "Тактика",  cls: "tactics" },
};

const PHASE_LABEL = {
  lobby: "Ожидание команд",
  challenge: "Космический вызов",
  upgrade: "Техобслуживание",
  battle: "Звёздная битва",
  finished: "Игра завершена",
};

const ACTION_LABEL = {
  attack: "Атака", scout: "Разведка", repair: "Восстановление",
};

const SERVICE_META = {
  shield:      { icon: "🛡️", label: "Щит",              price: 50 },
  mass_attack: { icon: "💥", label: "Массовая атака",    price: 80 },
  theft:       { icon: "💰", label: "Кража",             price: 60 },
  boost:       { icon: "⚡", label: "Ускорение",          price: 40 },
  double:      { icon: "🌀", label: "Двойной ход",        price: 70 },
};

async function apiPost(path, body) {
  const res = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || "Ошибка запроса");
  }
  return data;
}

async function apiGet(path) {
  const res = await fetch(API_BASE + path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "Ошибка запроса");
  return data;
}

function wsUrl(roomCode, token, role) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/${roomCode}?token=${encodeURIComponent(token)}&role=${role}`;
}

function connectWs(roomCode, token, role, onState, onClose) {
  let ws;
  let closedByUs = false;
  function open() {
    ws = new WebSocket(wsUrl(roomCode, token, role));
    ws.onmessage = (ev) => {
      try { onState(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
    };
    ws.onclose = () => {
      if (!closedByUs) {
        if (onClose) onClose();
        setTimeout(open, 1500); // авто-переподключение
      }
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
    // ping для поддержания соединения
    const pingInt = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      else clearInterval(pingInt);
    }, 25000);
  }
  open();
  return { close: () => { closedByUs = true; if (ws) ws.close(); } };
}

function toast(msg, isError) {
  const el = document.createElement("div");
  el.className = "toast";
  el.style.borderColor = isError ? "var(--danger)" : "var(--card-border)";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

function moduleBar(label, cls, level) {
  const pct = (level / 5) * 100;
  return `
    <div class="module-row">
      <div class="module-label">${label}</div>
      <div class="bar-bg"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div>
      <div class="module-lvl">${level}</div>
    </div>`;
}

function moduleBarHidden(label, cls) {
  return `
    <div class="module-row">
      <div class="module-label">${label}</div>
      <div class="bar-bg"><div class="bar-fill ${cls}" style="width:0%; opacity:.25"></div></div>
      <div class="module-lvl hidden-field">?</div>
    </div>`;
}

function upgradeCostText(level) {
  const table = { 1: 50, 2: 150, 3: 300, 4: 500 };
  if (level >= 5) return "макс.";
  return table[level] + " монет";
}

function serviceBadges(team) {
  // Иконки купленных разовых услуг у корабля на доске.
  let html = "";
  if (team.bought_shield) html += `<span title="Щит куплен" class="svc-badge${team.shield_active ? ' active' : ''}">🛡️</span>`;
  if (team.bought_mass_attack) html += `<span title="Массовая атака куплена" class="svc-badge">💥</span>`;
  if (team.bought_theft) html += `<span title="Кража куплена" class="svc-badge">💰</span>`;
  if (team.bought_boost) html += `<span title="Ускорение куплено" class="svc-badge${team.free_upgrade_charges > 0 ? ' active' : ''}">⚡</span>`;
  if (team.bought_double) html += `<span title="Двойной ход куплен" class="svc-badge${team.double_action_charges > 0 ? ' active' : ''}">🌀</span>`;
  return html ? `<div class="svc-badges">${html}</div>` : "";
}

const RULES_HTML = `
  <h2>Правила игры «Зефирные космолёты» 🚀🍬</h2>
  <p>До 6 команд соревнуются за несколько раундов. Каждый раунд состоит из трёх фаз:</p>
  <ol>
    <li><b>Космический вызов</b> — ведущий даёт задание вживую, команды получают монеты по результату.</li>
    <li><b>Техобслуживание</b> — команды тратят монеты на прокачку модулей корабля или покупку разовых услуг.</li>
    <li><b>Звёздная битва</b> — команды выбирают действия: атаку, разведку и/или восстановление.</li>
  </ol>
  <h3>Модули корабля (макс. уровень 5)</h3>
  <table>
    <tr><th>Модуль</th><th>Что даёт</th></tr>
    <tr><td>Атака</td><td>Урон = уровень атаки − уровень защиты цели</td></tr>
    <tr><td>Защита</td><td>Снижает получаемый урон. Также чем выше уровень, тем больше скрыто от других на доске: 2+ скрывает монеты, 3+ — атаку, 4+ — энергию, 5+ — тактику.</td></tr>
    <tr><td>Энергия</td><td>На уровне 3: второе действие в битве. На уровне 5: +10 монет в начале каждого раунда.</td></tr>
    <tr><td>Тактика</td><td>На уровне 3: вы можете выбрать модуль цели при атаке (вместо случайного). На уровне 5: скидка 20% на все разовые услуги.</td></tr>
  </table>
  <h3>Стоимость улучшения</h3>
  <table>
    <tr><th>Уровень</th><th>Стоимость</th></tr>
    <tr><td>1 → 2</td><td>50 монет</td></tr>
    <tr><td>2 → 3</td><td>150 монет</td></tr>
    <tr><td>3 → 4</td><td>300 монет</td></tr>
    <tr><td>4 → 5</td><td>500 монет</td></tr>
  </table>
  <h3>Разовые услуги (покупаются в фазе «Техобслуживание», каждая — один раз за игру)</h3>
  <table>
    <tr><th>Услуга</th><th>Стоимость</th><th>Эффект</th></tr>
    <tr><td>🛡️ Щит</td><td>50</td><td>Полностью блокирует одну следующую атаку по вам (любую, целиком), затем расходуется.</td></tr>
    <tr><td>💥 Массовая атака</td><td>80</td><td>Наносит урон всем противникам сразу (щит поглощает удар и снимается, урон не проходит).</td></tr>
    <tr><td>💰 Кража</td><td>60</td><td>Украсть 30 монет у случайной команды (если у неё больше 0).</td></tr>
    <tr><td>⚡ Ускорение</td><td>40</td><td>Следующее улучшение модуля бесплатно.</td></tr>
    <tr><td>🌀 Двойной ход</td><td>70</td><td>В следующей битве можно совершить два боевых действия (атака/восстановление) вместо одного.</td></tr>
  </table>
  <h3>Действия в «Звёздной битве»</h3>
  <table>
    <tr><th>Действие</th><th>Стоимость</th><th>Эффект</th></tr>
    <tr><td>Атака</td><td>30 монет</td><td>Снижает случайный модуль цели на 1 уровень (или выбранный, если есть Тактика 3+). Полностью блокируется щитом цели.</td></tr>
    <tr><td>Разведка</td><td>Бесплатно</td><td>Полностью раскрывает корабль цели до конца раунда. Доступна всем без ограничений.</td></tr>
    <tr><td>Восстановление</td><td>40 монет</td><td>Восстанавливает на 1 уровень модуль, который был повреждён атакой (не выше исходного уровня, и только повреждённые модули).</td></tr>
  </table>
  <p class="muted">Атака и восстановление — общий пул из 1 действия за раунд (больше — от Энергии 3+ и купленного Двойного хода). Разведка не расходует этот пул. После использования действия кнопка блокируется до следующего раунда.</p>
`;

function openRules() {
  const overlay = document.createElement("div");
  overlay.className = "rules-overlay";
  overlay.innerHTML = `
    <div class="card rules-panel" style="position:relative;">
      <button class="ghost close-x" id="rulesClose">✕</button>
      ${RULES_HTML}
    </div>`;
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  overlay.querySelector("#rulesClose").addEventListener("click", () => overlay.remove());
}
