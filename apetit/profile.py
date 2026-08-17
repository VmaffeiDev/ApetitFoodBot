"""Cadastro do funcionario.

O vinculo aqui e triplo: a unidade da Apetit que serve, a empresa onde a pessoa
trabalha e o setor dentro dela. Isso melhora o cardapio certo para a pessoa
certa, e ao mesmo tempo aumenta muito o risco de reidentificacao: setor pequeno
mais dado alimentar identifica alguem sem precisar do nome. Por isso qualquer
leitura agregada passa por `aggregate_by_sector`, que aplica n minimo.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .allergens import ALLERGENS, Restriction, RestrictionKind

# Abaixo disso, um recorte nao vai para relatorio: com poucas pessoas no setor,
# o "agregado" volta a ser individual.
MIN_AGGREGATE = 5


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Employee:
    telegram_id: int
    name: str
    apetit_unit: str = ""       # unidade/contrato da Apetit que serve o refeitorio
    client_company: str = ""    # empresa onde a pessoa trabalha
    sector: str = ""            # setor dentro da empresa
    goal: str = ""
    consent_accepted: bool = False
    consented_at: str = ""
    restrictions: list[Restriction] = field(default_factory=list)
    # Termos que a pessoa escreveu e que nao existem como campo em ficha
    # tecnica. Guardados para virar aviso, nunca descartados em silencio.
    free_restrictions: list[str] = field(default_factory=list)
    # Alimentos que a pessoa so prefere evitar. Bloqueiam quando aparecem no
    # nome do prato, mas nao geram aviso no resto do cardapio.
    avoid_foods: list[str] = field(default_factory=list)

    @property
    def registered(self) -> bool:
        """Cadastro so vale com consentimento e com o vinculo completo."""
        return bool(
            self.consent_accepted
            and self.name.strip()
            and self.apetit_unit.strip()
            and self.client_company.strip()
            and self.sector.strip()
        )

    def missing_fields(self) -> list[str]:
        faltando = []
        for campo, rotulo in (
            ("name", "nome"),
            ("apetit_unit", "unidade da Apetit"),
            ("client_company", "empresa"),
            ("sector", "setor"),
        ):
            if not str(getattr(self, campo)).strip():
                faltando.append(rotulo)
        if not self.consent_accepted:
            faltando.append("aceite do termo de privacidade")
        return faltando


def save_employee(conn: sqlite3.Connection, employee: Employee) -> None:
    timestamp = now_iso()
    consent_at = employee.consented_at or (timestamp if employee.consent_accepted else "")
    conn.execute(
        """
        INSERT INTO employee (
            telegram_id, name, apetit_unit, client_company, sector, goal,
            consent_accepted, consented_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            name = excluded.name,
            apetit_unit = excluded.apetit_unit,
            client_company = excluded.client_company,
            sector = excluded.sector,
            goal = excluded.goal,
            consent_accepted = excluded.consent_accepted,
            consented_at = excluded.consented_at,
            updated_at = excluded.updated_at
        """,
        (
            employee.telegram_id,
            employee.name,
            employee.apetit_unit,
            employee.client_company,
            employee.sector,
            employee.goal,
            int(employee.consent_accepted),
            consent_at,
            timestamp,
            timestamp,
        ),
    )
    conn.execute("DELETE FROM employee_restriction WHERE telegram_id = ?", (employee.telegram_id,))
    conn.execute("DELETE FROM employee_free_restriction WHERE telegram_id = ?", (employee.telegram_id,))
    for termo in employee.free_restrictions:
        if termo.strip():
            conn.execute(
                "INSERT INTO employee_free_restriction (telegram_id, term, kind, created_at) VALUES (?, ?, ?, ?)",
                (employee.telegram_id, termo.strip(), "alergia", timestamp),
            )
    for termo in employee.avoid_foods:
        if termo.strip():
            conn.execute(
                "INSERT INTO employee_free_restriction (telegram_id, term, kind, created_at) VALUES (?, ?, ?, ?)",
                (employee.telegram_id, termo.strip(), "evitar", timestamp),
            )
    for restriction in employee.restrictions:
        if restriction.allergen not in ALLERGENS:
            raise ValueError(f"Alergenico desconhecido: {restriction.allergen}")
        conn.execute(
            """
            INSERT INTO employee_restriction (telegram_id, allergen_code, kind, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (employee.telegram_id, restriction.allergen, restriction.kind.value, timestamp),
        )
    conn.commit()


def load_employee(conn: sqlite3.Connection, telegram_id: int) -> Employee | None:
    row = conn.execute("SELECT * FROM employee WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if not row:
        return None
    restricoes = [
        Restriction(r["allergen_code"], RestrictionKind(r["kind"]))
        for r in conn.execute(
            "SELECT allergen_code, kind FROM employee_restriction WHERE telegram_id = ? ORDER BY allergen_code",
            (telegram_id,),
        ).fetchall()
    ]
    linhas_livres = conn.execute(
        "SELECT term, kind FROM employee_free_restriction WHERE telegram_id = ? ORDER BY term",
        (telegram_id,),
    ).fetchall()
    livres = [r["term"] for r in linhas_livres if r["kind"] != "evitar"]
    evitar = [r["term"] for r in linhas_livres if r["kind"] == "evitar"]
    return Employee(
        telegram_id=row["telegram_id"],
        name=row["name"],
        apetit_unit=row["apetit_unit"],
        client_company=row["client_company"],
        sector=row["sector"],
        goal=row["goal"],
        consent_accepted=bool(row["consent_accepted"]),
        consented_at=row["consented_at"],
        restrictions=restricoes,
        free_restrictions=livres,
        avoid_foods=evitar,
    )


def delete_employee_data(conn: sqlite3.Connection, telegram_id: int) -> None:
    """Exclusao completa pedida pelo titular: consumo, pontos e avaliacoes.

    As avaliacoes do refeitorio saem junto. Elas ja circulam so agregadas, mas
    quem pede exclusao esta pedindo que nao reste linha ligada a ele — e a
    linha existe, com telegram_id, mesmo que nenhum relatorio a leia assim.
    """
    conn.execute(
        "DELETE FROM service_rating_tag WHERE rating_id IN "
        "(SELECT id FROM service_rating WHERE telegram_id = ?)",
        (telegram_id,),
    )
    for tabela in (
        "service_rating", "points_event", "favorite", "consumption",
        "employee_free_restriction", "employee_restriction", "employee",
    ):
        conn.execute(f"DELETE FROM {tabela} WHERE telegram_id = ?", (telegram_id,))
    conn.commit()


def known_companies(conn: sqlite3.Connection, apetit_unit: str = "") -> list[str]:
    """Empresas que ja aparecem em cadastros, para virar botao em vez de digitacao."""
    where, params = [], []
    if apetit_unit:
        where.append("apetit_unit = ?")
        params.append(apetit_unit)
    filtro = f"WHERE {' AND '.join(where)}" if where else ""
    linhas = conn.execute(
        f"SELECT client_company, COUNT(*) AS total FROM employee {filtro} "
        "GROUP BY client_company ORDER BY total DESC, client_company",
        params,
    ).fetchall()
    return [linha["client_company"] for linha in linhas if linha["client_company"].strip()]


def known_sectors(conn: sqlite3.Connection, client_company: str) -> list[str]:
    linhas = conn.execute(
        "SELECT sector, COUNT(*) AS total FROM employee WHERE client_company = ? "
        "GROUP BY sector ORDER BY total DESC, sector",
        (client_company,),
    ).fetchall()
    return [linha["sector"] for linha in linhas if linha["sector"].strip()]


def aggregate_by_sector(
    conn: sqlite3.Connection,
    client_company: str = "",
    min_size: int = MIN_AGGREGATE,
) -> list[dict]:
    """Contagem por setor para a empresa, suprimindo recortes pequenos.

    Nao expoe nome, objetivo individual nem consumo: so quantas pessoas usam.
    Setor com menos de `min_size` pessoas sai como 'suprimido', porque em um
    setor de duas pessoas qualquer media vira dado individual.
    """
    where, params = [], []
    if client_company:
        where.append("client_company = ?")
        params.append(client_company)
    filtro = f"WHERE {' AND '.join(where)}" if where else ""
    linhas = conn.execute(
        f"""
        SELECT client_company, sector, COUNT(*) AS total
        FROM employee
        {filtro}
        GROUP BY client_company, sector
        ORDER BY client_company, sector
        """,
        params,
    ).fetchall()

    resultado = []
    for linha in linhas:
        if linha["total"] < min_size:
            resultado.append(
                {
                    "client_company": linha["client_company"],
                    "sector": linha["sector"],
                    "total": None,
                    "suprimido": True,
                    "motivo": f"menos de {min_size} pessoas no recorte",
                }
            )
            continue
        resultado.append(
            {
                "client_company": linha["client_company"],
                "sector": linha["sector"],
                "total": linha["total"],
                "suprimido": False,
                "motivo": "",
            }
        )
    return resultado
