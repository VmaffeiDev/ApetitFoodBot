"""Registro de consumo, pratos favoritos e pontuacao.

Sobre a pontuacao: num app corporativo, premiar deficit calorico ou perda de
peso empurra o funcionario para uma relacao ruim com comida, e ainda por cima
sob o olhar do empregador. Entao nenhuma regra aqui pontua comer menos.

O que pontua e constancia (registrar), composicao (incluir salada ou fruta),
variedade e atingir proteina — coisas que a pessoa controla e que nao viram
competicao de quem come menos. Cada regra carrega o campo `basis` justamente
para deixar isso auditavel, e ha teste garantindo que nenhuma regra se apoie
em deficit ou peso.

Nao existe ranking entre colegas de proposito: comparacao publica de habito
alimentar dentro da empresa e onde isso vira constrangimento.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

BASES_PROIBIDAS = {"deficit", "peso", "emagrecimento", "restricao_calorica"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Rule:
    code: str
    label: str
    points: int
    basis: str          # o que a regra premia; nunca deficit nem peso
    period: str = "dia"  # "dia" ou "semana"


RULES: tuple[Rule, ...] = (
    Rule("registro", "Registrou a refeicao", 10, "constancia"),
    Rule("composicao", "Incluiu salada ou fruta no prato", 5, "composicao"),
    Rule("proteina", "Atingiu a meta de proteina do dia", 10, "meta_nutricional"),
    Rule("variedade", "Comeu pratos variados na semana", 15, "variedade", period="semana"),
    Rule("sequencia", "Registrou a semana inteira", 20, "constancia", period="semana"),
)

RULES_BY_CODE = {rule.code: rule for rule in RULES}

# Categorias que contam como composicao melhor no prato.
CATEGORIAS_FRESCAS = {"SALADA", "FRUTA"}


def _snapshot(conn: sqlite3.Connection, service_date: str, code: str) -> dict:
    """Congela como o prato estava no momento do registro.

    Nome, categoria e macros saem daqui e vao gravados junto do consumo. Sem
    isso, corrigir uma ficha tecnica em novembro mudaria retroativamente o que
    a pessoa comeu em setembro — e historico que muda sozinho nao e historico.
    """
    linha = conn.execute(
        """
        SELECT i.name, i.kcal, i.cho_g, i.lip_g, i.ptn_g,
               (SELECT e.category FROM menu_entry e
                 WHERE e.item_code = i.code AND e.service_date = ?
                 LIMIT 1) AS category
        FROM menu_item i
        WHERE i.code = ?
        """,
        (service_date, code),
    ).fetchone()
    if not linha:
        # Item que sumiu da base: guarda o codigo para a pessoa nao perder o
        # registro, e deixa os macros nulos em vez de inventar zero.
        return {"name": code, "category": "", "kcal": None, "cho_g": None, "lip_g": None, "ptn_g": None}
    return dict(linha)


def log_consumption(
    conn: sqlite3.Connection,
    telegram_id: int,
    service_date: str,
    item_codes: list[str],
    meal: str = "almoco",
    source: str = "montado",
) -> None:
    """Registra a refeicao do dia. Regravar o mesmo dia substitui o registro.

    Codigo repetido em `item_codes` vira quantidade: ["arroz", "arroz"] e
    "duas colheres de arroz", que e como a sugestao de porcao fala.
    """
    conn.execute(
        "DELETE FROM consumption WHERE telegram_id = ? AND service_date = ? AND meal = ?",
        (telegram_id, service_date, meal),
    )
    quantidades: dict[str, int] = {}
    for code in item_codes:
        quantidades[code] = quantidades.get(code, 0) + 1

    timestamp = now_iso()
    for code, quantidade in quantidades.items():
        foto = _snapshot(conn, service_date, code)
        conn.execute(
            """
            INSERT INTO consumption (
                telegram_id, service_date, meal, item_code, item_name, category,
                quantity, kcal, cho_g, lip_g, ptn_g, source, logged_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id, service_date, meal, code, foto["name"], foto["category"] or "",
                quantidade, foto["kcal"], foto["cho_g"], foto["lip_g"], foto["ptn_g"],
                source, timestamp,
            ),
        )
    conn.commit()


def consumption_totals(conn: sqlite3.Connection, telegram_id: int, service_date: str) -> dict:
    """Totais do dia, lidos da fotografia — nao da ficha tecnica de hoje."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(quantity), 0) AS itens,
            COALESCE(SUM(kcal * quantity), 0) AS kcal,
            COALESCE(SUM(cho_g * quantity), 0) AS cho_g,
            COALESCE(SUM(lip_g * quantity), 0) AS lip_g,
            COALESCE(SUM(ptn_g * quantity), 0) AS ptn_g,
            SUM(CASE WHEN kcal IS NULL THEN 1 ELSE 0 END) AS sem_macro
        FROM consumption
        WHERE telegram_id = ? AND service_date = ?
        """,
        (telegram_id, service_date),
    ).fetchone()
    return dict(row) if row else {}


def consumption_history(conn: sqlite3.Connection, telegram_id: int, limit: int = 30) -> list[sqlite3.Row]:
    """Tudo o que a pessoa ja registrou, do mais recente para o mais antigo."""
    return conn.execute(
        """
        SELECT service_date, meal, item_code, item_name AS name, category,
               quantity, kcal, ptn_g, source, logged_at
        FROM consumption
        WHERE telegram_id = ?
        ORDER BY service_date DESC, item_name
        LIMIT ?
        """,
        (telegram_id, limit),
    ).fetchall()


@dataclass
class DayRecord:
    """Um dia do historico, do jeito que a pessoa quer reler: o prato inteiro."""

    service_date: str
    items: list[sqlite3.Row]

    @property
    def kcal(self) -> float:
        return sum((linha["kcal"] or 0) * linha["quantity"] for linha in self.items)

    @property
    def ptn_g(self) -> float:
        return sum((linha["ptn_g"] or 0) * linha["quantity"] for linha in self.items)

    @property
    def incomplete(self) -> bool:
        """Algum item registrado nao tinha macro. O total do dia e parcial."""
        return any(linha["kcal"] is None for linha in self.items)


def history_by_day(conn: sqlite3.Connection, telegram_id: int, days: int = 14) -> list[DayRecord]:
    """Historico agrupado por dia, do mais recente para o mais antigo."""
    datas = [
        linha["service_date"]
        for linha in conn.execute(
            """
            SELECT DISTINCT service_date FROM consumption
            WHERE telegram_id = ?
            ORDER BY service_date DESC
            LIMIT ?
            """,
            (telegram_id, days),
        ).fetchall()
    ]
    if not datas:
        return []
    marcadores = ",".join("?" for _ in datas)
    linhas = conn.execute(
        f"""
        SELECT service_date, meal, item_code, item_name AS name, category,
               quantity, kcal, ptn_g, source
        FROM consumption
        WHERE telegram_id = ? AND service_date IN ({marcadores})
        ORDER BY service_date DESC, item_name
        """,
        [telegram_id, *datas],
    ).fetchall()
    agrupado: dict[str, list[sqlite3.Row]] = {data: [] for data in datas}
    for linha in linhas:
        agrupado[linha["service_date"]].append(linha)
    return [DayRecord(data, agrupado[data]) for data in datas]


def add_favorite(conn: sqlite3.Connection, telegram_id: int, item_code: str) -> None:
    conn.execute(
        """
        INSERT INTO favorite (telegram_id, item_code, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_id, item_code) DO NOTHING
        """,
        (telegram_id, item_code, now_iso()),
    )
    conn.commit()


def remove_favorite(conn: sqlite3.Connection, telegram_id: int, item_code: str) -> None:
    conn.execute("DELETE FROM favorite WHERE telegram_id = ? AND item_code = ?", (telegram_id, item_code))
    conn.commit()


def favorites(conn: sqlite3.Connection, telegram_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT f.item_code, i.name, i.kcal, i.ptn_g
        FROM favorite f
        JOIN menu_item i ON i.code = f.item_code
        WHERE f.telegram_id = ?
        ORDER BY i.name
        """,
        (telegram_id,),
    ).fetchall()


def favorites_returning(
    conn: sqlite3.Connection,
    from_date: str,
    to_date: str,
    apetit_unit: str = "",
) -> list[sqlite3.Row]:
    """Quem favoritou um prato que volta ao cardapio no periodo.

    E o gancho do aviso: assim que o cardapio da semana entra, sai a mensagem
    para quem esperava aquele prato.
    """
    where = ["e.service_date BETWEEN ? AND ?"]
    params: list[str] = [from_date, to_date]
    if apetit_unit:
        where.append("e.unit = ?")
        params.append(apetit_unit)
    return conn.execute(
        f"""
        SELECT f.telegram_id, emp.name AS employee_name, i.name AS item_name,
               e.service_date, e.unit
        FROM favorite f
        JOIN menu_entry e ON e.item_code = f.item_code
        JOIN menu_item i ON i.code = f.item_code
        JOIN employee emp ON emp.telegram_id = f.telegram_id
        WHERE {' AND '.join(where)}
        ORDER BY e.service_date, emp.name
        """,
        params,
    ).fetchall()


def _award(conn: sqlite3.Connection, telegram_id: int, rule: Rule, reference: str, detail: str = "") -> bool:
    """Concede a regra uma unica vez por periodo. Devolve se pontuou agora."""
    cursor = conn.execute(
        """
        INSERT INTO points_event (telegram_id, rule_code, points, reference_date, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id, rule_code, reference_date) DO NOTHING
        """,
        (telegram_id, rule.code, rule.points, reference, detail, now_iso()),
    )
    return cursor.rowcount > 0


def score_day(
    conn: sqlite3.Connection,
    telegram_id: int,
    service_date: str,
    protein_target_g: float | None = None,
) -> list[Rule]:
    """Avalia as regras do dia. Reexecutar nao duplica pontos."""
    itens = conn.execute(
        """
        SELECT category, quantity, ptn_g
        FROM consumption
        WHERE telegram_id = ? AND service_date = ?
        """,
        (telegram_id, service_date),
    ).fetchall()
    if not itens:
        return []

    concedidas: list[Rule] = []

    if _award(conn, telegram_id, RULES_BY_CODE["registro"], service_date):
        concedidas.append(RULES_BY_CODE["registro"])

    categorias = {(linha["category"] or "").upper() for linha in itens}
    if categorias & CATEGORIAS_FRESCAS:
        if _award(conn, telegram_id, RULES_BY_CODE["composicao"], service_date):
            concedidas.append(RULES_BY_CODE["composicao"])

    if protein_target_g:
        total_ptn = sum((linha["ptn_g"] or 0) * linha["quantity"] for linha in itens)
        if total_ptn >= protein_target_g:
            detalhe = f"{total_ptn:.1f} g de {protein_target_g:.0f} g"
            if _award(conn, telegram_id, RULES_BY_CODE["proteina"], service_date, detalhe):
                concedidas.append(RULES_BY_CODE["proteina"])

    conn.commit()
    return concedidas


def score_week(conn: sqlite3.Connection, telegram_id: int, week_start: str) -> list[Rule]:
    """Regras semanais: variedade e semana registrada por inteiro."""
    inicio = date.fromisoformat(week_start)
    fim = inicio + timedelta(days=6)
    linhas = conn.execute(
        """
        SELECT DISTINCT service_date, item_code
        FROM consumption
        WHERE telegram_id = ? AND service_date BETWEEN ? AND ?
        """,
        (telegram_id, inicio.isoformat(), fim.isoformat()),
    ).fetchall()
    if not linhas:
        return []

    concedidas: list[Rule] = []
    itens_distintos = {linha["item_code"] for linha in linhas}
    dias_registrados = {linha["service_date"] for linha in linhas}

    if len(itens_distintos) >= 6:
        detalhe = f"{len(itens_distintos)} pratos diferentes"
        if _award(conn, telegram_id, RULES_BY_CODE["variedade"], week_start, detalhe):
            concedidas.append(RULES_BY_CODE["variedade"])

    uteis = {(inicio + timedelta(days=d)).isoformat() for d in range(5)}
    if uteis <= dias_registrados:
        if _award(conn, telegram_id, RULES_BY_CODE["sequencia"], week_start):
            concedidas.append(RULES_BY_CODE["sequencia"])

    conn.commit()
    return concedidas


def total_points(conn: sqlite3.Connection, telegram_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(points), 0) AS total FROM points_event WHERE telegram_id = ?",
        (telegram_id,),
    ).fetchone()
    return int(row["total"])


def points_breakdown(conn: sqlite3.Connection, telegram_id: int) -> list[sqlite3.Row]:
    """Extrato dos pontos: a pessoa tem que conseguir ver por que ganhou."""
    return conn.execute(
        """
        SELECT rule_code, SUM(points) AS pontos, COUNT(*) AS vezes
        FROM points_event
        WHERE telegram_id = ?
        GROUP BY rule_code
        ORDER BY pontos DESC
        """,
        (telegram_id,),
    ).fetchall()
