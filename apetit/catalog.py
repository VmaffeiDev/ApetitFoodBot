"""Persistencia do cardapio e da ficha tecnica.

O item so e publicado se passar na validacao. O que nao passa fica registrado
em menu_import_issue para o nutricionista revisar, em vez de ir para o app do
funcionario com macro errado.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .allergens import ALLERGENS, Declaration, Restriction, check_item, coverage
from .csv_import import parse_menu_csv
from .model import Issue, MenuEntry
from .validation import validate_item

SCHEMA = """
CREATE TABLE IF NOT EXISTS menu_item (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    portion_g REAL,
    kcal REAL,
    cho_g REAL,
    lip_g REAL,
    ptn_g REAL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS menu_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit TEXT NOT NULL,
    service_date TEXT NOT NULL,
    meal TEXT NOT NULL,
    category TEXT NOT NULL,
    slot INTEGER NOT NULL DEFAULT 1,
    item_code TEXT NOT NULL REFERENCES menu_item(code),
    created_at TEXT NOT NULL,
    UNIQUE (unit, service_date, meal, category, slot)
);
CREATE INDEX IF NOT EXISTS idx_menu_entry_date ON menu_entry (service_date, unit, meal);
CREATE TABLE IF NOT EXISTS menu_import_issue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch TEXT NOT NULL,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    detail TEXT NOT NULL,
    item_name TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    service_date TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- Alergenico declarado por prato. A ausencia de linha significa "nao declarado",
-- que o app trata como incerteza, nunca como liberacao.
CREATE TABLE IF NOT EXISTS menu_item_allergen (
    item_code TEXT NOT NULL REFERENCES menu_item(code),
    allergen_code TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (item_code, allergen_code)
);

CREATE TABLE IF NOT EXISTS employee (
    telegram_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    apetit_unit TEXT NOT NULL DEFAULT '',
    client_company TEXT NOT NULL DEFAULT '',
    sector TEXT NOT NULL DEFAULT '',
    goal TEXT NOT NULL DEFAULT '',
    consent_accepted INTEGER NOT NULL DEFAULT 0,
    consented_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_employee_org ON employee (client_company, sector);

CREATE TABLE IF NOT EXISTS employee_restriction (
    telegram_id INTEGER NOT NULL REFERENCES employee(telegram_id),
    allergen_code TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'alergia',
    created_at TEXT NOT NULL,
    PRIMARY KEY (telegram_id, allergen_code)
);

CREATE TABLE IF NOT EXISTS consumption (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    service_date TEXT NOT NULL,
    meal TEXT NOT NULL DEFAULT 'almoco',
    item_code TEXT NOT NULL REFERENCES menu_item(code),
    logged_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_consumption_pessoa ON consumption (telegram_id, service_date);

CREATE TABLE IF NOT EXISTS favorite (
    telegram_id INTEGER NOT NULL,
    item_code TEXT NOT NULL REFERENCES menu_item(code),
    created_at TEXT NOT NULL,
    PRIMARY KEY (telegram_id, item_code)
);

-- A restricao unica e o que torna a pontuacao idempotente: reavaliar o mesmo
-- dia nao concede pontos de novo.
CREATE TABLE IF NOT EXISTS points_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    rule_code TEXT NOT NULL,
    points INTEGER NOT NULL,
    reference_date TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (telegram_id, rule_code, reference_date)
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


class ImportResult:
    def __init__(self, batch: str) -> None:
        self.batch = batch
        self.published: list[MenuEntry] = []
        self.blocked: list[MenuEntry] = []
        self.issues: list[Issue] = []

    @property
    def blocking_issues(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.blocking]

    def grouped_issues(self) -> list[tuple[Issue, int, list[str]]]:
        """Agrupa por ficha tecnica.

        Um mesmo item errado reaparece em varios dias do mes. Quem revisa
        corrige a ficha uma vez, entao a fila precisa listar item, nao ocorrencia.
        """
        grupos: dict[tuple[str, str], tuple[Issue, list[str]]] = {}
        for issue in self.issues:
            chave = (issue.code, issue.item_name)
            if chave not in grupos:
                grupos[chave] = (issue, [])
            if issue.service_date:
                grupos[chave][1].append(issue.service_date)
        ordenado = sorted(grupos.values(), key=lambda par: (not par[0].blocking, par[0].item_name))
        return [(issue, len(datas), sorted(datas)) for issue, datas in ordenado]

    def summary(self) -> str:
        agrupados = self.grouped_issues()
        bloqueios = sum(1 for issue, *_ in agrupados if issue.blocking)
        linhas = [
            f"Lote: {self.batch}",
            f"Itens publicados: {len(self.published)}",
            f"Ocorrencias bloqueadas: {len(self.blocked)} (em {bloqueios} ficha(s) tecnica(s))",
            f"Avisos: {len(agrupados) - bloqueios}",
        ]
        if agrupados:
            linhas.append("")
            linhas.append("Para revisao do nutricionista:")
            for issue, vezes, datas in agrupados:
                marca = "BLOQUEIO" if issue.blocking else "aviso   "
                nome = issue.item_name or issue.category or "(cardapio)"
                repete = f" — {vezes}x no periodo (1o em {datas[0]})" if vezes > 1 else ""
                linhas.append(f"  [{marca}] {nome}{repete}")
                linhas.append(f"             {issue.detail}")
        return "\n".join(linhas)


def import_menu_csv(
    conn: sqlite3.Connection,
    text: str,
    unit: str = "",
    meal: str = "almoco",
    batch: str = "",
) -> ImportResult:
    """Le, valida e publica o cardapio. Item bloqueado nao chega ao cardapio."""
    batch = batch or now_iso()
    result = ImportResult(batch)

    entries, issues = parse_menu_csv(text, unit=unit, meal=meal)
    result.issues.extend(issues)

    for entry in entries:
        entry_issues = validate_item(
            entry.item,
            unit=entry.unit,
            service_date=entry.service_date,
            category=entry.category,
        )
        result.issues.extend(entry_issues)
        if any(issue.blocking for issue in entry_issues):
            result.blocked.append(entry)
            continue
        result.published.append(entry)

    timestamp = now_iso()
    for entry in result.published:
        item = entry.item
        conn.execute(
            """
            INSERT INTO menu_item (code, name, portion_g, kcal, cho_g, lip_g, ptn_g, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                portion_g = COALESCE(excluded.portion_g, menu_item.portion_g),
                kcal = excluded.kcal,
                cho_g = excluded.cho_g,
                lip_g = excluded.lip_g,
                ptn_g = excluded.ptn_g,
                updated_at = excluded.updated_at
            """,
            (
                item.code,
                item.name,
                item.portion_g,
                item.nutrition.kcal,
                item.nutrition.cho_g,
                item.nutrition.lip_g,
                item.nutrition.ptn_g,
                timestamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO menu_entry (unit, service_date, meal, category, slot, item_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(unit, service_date, meal, category, slot) DO UPDATE SET
                item_code = excluded.item_code,
                created_at = excluded.created_at
            """,
            (entry.unit, entry.service_date, entry.meal, entry.category, entry.slot, item.code, timestamp),
        )
        if item.allergens:
            set_item_allergens(conn, item.code, item.allergens, source="importacao")

    for issue in result.issues:
        conn.execute(
            """
            INSERT INTO menu_import_issue
                (batch, severity, code, detail, item_name, unit, service_date, category, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch,
                issue.severity,
                issue.code,
                issue.detail,
                issue.item_name,
                issue.unit,
                issue.service_date,
                issue.category,
                timestamp,
            ),
        )
    conn.commit()
    return result


def menu_for_date(conn: sqlite3.Connection, service_date: str, unit: str = "", meal: str = "") -> list[sqlite3.Row]:
    where = ["e.service_date = ?"]
    params: list[str] = [service_date]
    if unit:
        where.append("e.unit = ?")
        params.append(unit)
    if meal:
        where.append("e.meal = ?")
        params.append(meal)
    return conn.execute(
        f"""
        SELECT e.category, e.slot, e.meal, e.unit, e.item_code,
               i.name, i.portion_g, i.kcal, i.cho_g, i.lip_g, i.ptn_g
        FROM menu_entry e
        JOIN menu_item i ON i.code = e.item_code
        WHERE {' AND '.join(where)}
        ORDER BY e.meal, e.category, e.slot
        """,
        params,
    ).fetchall()


def set_item_allergens(
    conn: sqlite3.Connection,
    item_code: str,
    declarations: dict[str, str],
    source: str = "ficha tecnica",
) -> None:
    """Grava a declaracao de alergenicos de um prato."""
    timestamp = now_iso()
    for allergen_code, status in declarations.items():
        if allergen_code not in ALLERGENS:
            raise ValueError(f"Alergenico desconhecido: {allergen_code}")
        Declaration(status)  # valida o estado
        conn.execute(
            """
            INSERT INTO menu_item_allergen (item_code, allergen_code, status, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_code, allergen_code) DO UPDATE SET
                status = excluded.status,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (item_code, allergen_code, Declaration(status).value, source, timestamp),
        )
    conn.commit()


def item_allergens(conn: sqlite3.Connection, item_code: str) -> dict[str, Declaration]:
    linhas = conn.execute(
        "SELECT allergen_code, status FROM menu_item_allergen WHERE item_code = ?",
        (item_code,),
    ).fetchall()
    return {linha["allergen_code"]: Declaration(linha["status"]) for linha in linhas}


def check_menu_for_employee(
    conn: sqlite3.Connection,
    service_date: str,
    restrictions: list[Restriction],
    unit: str = "",
    meal: str = "",
) -> list[dict]:
    """Cardapio do dia ja conferido contra a ficha da pessoa.

    E o que sustenta o aviso no momento da escolha: cada item volta com o
    veredito e a mensagem, incluindo o caso em que nao da para afirmar nada.
    """
    resultado = []
    for linha in menu_for_date(conn, service_date, unit=unit, meal=meal):
        declarado = item_allergens(conn, linha["item_code"])
        verificacao = check_item(restrictions, declarado)
        resultado.append(
            {
                "category": linha["category"],
                "slot": linha["slot"],
                "item_code": linha["item_code"],
                "name": linha["name"],
                "kcal": linha["kcal"],
                "ptn_g": linha["ptn_g"],
                "check": verificacao,
                "allergen_coverage": coverage(declarado),
            }
        )
    return resultado


def items_with_allergens(conn: sqlite3.Connection) -> list[tuple[str, str, dict[str, Declaration]]]:
    """Todas as fichas publicadas com o que ja se sabe do alergenico delas.

    Ordenado por quantas vezes o prato aparece no cardapio: declarar primeiro o
    arroz que sai 22 vezes rende mais que o prato que saiu uma vez.
    """
    linhas = conn.execute(
        """
        SELECT i.code, i.name, COUNT(e.id) AS vezes
        FROM menu_item i
        LEFT JOIN menu_entry e ON e.item_code = i.code
        GROUP BY i.code, i.name
        ORDER BY vezes DESC, i.name
        """
    ).fetchall()
    return [(linha["code"], linha["name"], item_allergens(conn, linha["code"])) for linha in linhas]


def allergen_coverage(conn: sqlite3.Connection) -> dict:
    """Quanto do cardapio ja sustenta o alerta de alergia."""
    total = completos = parciais = 0
    faltando: list[tuple[str, str, int]] = []
    for code, name, declarado in items_with_allergens(conn):
        total += 1
        quantos = len(declarado)
        if quantos >= len(ALLERGENS):
            completos += 1
        elif quantos:
            parciais += 1
            faltando.append((code, name, len(ALLERGENS) - quantos))
        else:
            faltando.append((code, name, len(ALLERGENS)))
    return {"total": total, "completos": completos, "parciais": parciais, "faltando": faltando}


def apply_allergen_sheet(conn: sqlite3.Connection, declaracoes: dict[str, dict[str, str]]) -> tuple[int, list[str]]:
    """Grava a planilha preenchida. Devolve quantas fichas mudaram e os erros."""
    conhecidos = {linha["code"] for linha in conn.execute("SELECT code FROM menu_item").fetchall()}
    aplicadas = 0
    erros: list[str] = []
    for code, estados in declaracoes.items():
        if code not in conhecidos:
            erros.append(f"Prato desconhecido, ignorado: {code}")
            continue
        try:
            set_item_allergens(conn, code, estados, source="planilha do nutricionista")
        except ValueError as erro:
            erros.append(f"{code}: {erro}")
            continue
        aplicadas += 1
    return aplicadas, erros


def known_units(conn: sqlite3.Connection) -> list[str]:
    """Unidades que ja tem cardapio publicado.

    O funcionario nao sabe o codigo interno da operacao, entao o cadastro
    oferece o que existe em vez de pedir a sigla de cabeca.
    """
    linhas = conn.execute(
        "SELECT unit, COUNT(*) AS total FROM menu_entry GROUP BY unit ORDER BY total DESC, unit"
    ).fetchall()
    return [linha["unit"] for linha in linhas if linha["unit"].strip()]


def pending_issues(conn: sqlite3.Connection, batch: str = "") -> list[sqlite3.Row]:
    """Fila de revisao do nutricionista: o que foi barrado na importacao."""
    if batch:
        return conn.execute(
            "SELECT * FROM menu_import_issue WHERE severity = 'block' AND batch = ? ORDER BY id",
            (batch,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM menu_import_issue WHERE severity = 'block' ORDER BY id"
    ).fetchall()
