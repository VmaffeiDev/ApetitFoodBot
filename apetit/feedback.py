"""Avaliacao do refeitorio pelo funcionario que acabou de almocar.

Esta e a unica parte do app cujo dado existe **para a Apetit ler**. Todo o
resto — o que a pessoa come, seu objetivo, suas restricoes — e privado do
funcionario e a empresa nunca ve. Aqui o fluxo se inverte, e isso cria um risco
que o desenho precisa resolver, nao a politica de uso:

    quem reclama do refeitorio esta reclamando do servico contratado pela
    propria empresa onde trabalha. Se a avaliacao chegasse identificada, o
    funcionario que disse "faltou comida" ficaria exposto a retaliacao — e
    a proxima pessoa aprenderia a mentir na avaliacao.

Tres decisoes do modulo saem dai:

1. **A linha de avaliacao nao guarda empresa nem setor.** Ela e sobre o
   refeitorio. Guardar o setor criaria exatamente o cruzamento que reidentifica
   alguem ("a unica pessoa da manutencao que almocou terca").
2. **Nenhuma leitura para a gestao seleciona `pessoa_id`.** Ele existe na
   tabela so para tres coisas: uma avaliacao por dia, a pessoa poder rever e
   trocar a propria, e a exclusao total quando ela pedir (LGPD).
3. **Comentario so e liberado com volume.** Um comentario solto num dia de tres
   avaliacoes e um bilhete assinado. Abaixo do n minimo, o texto nao sai.

O que se mede sai do que a operacao consegue corrigir na segunda-feira: comida,
atendimento e falta. Nao ha nota de funcionario do balcao por nome — avaliacao
individual de trabalhador por trabalhador nao e problema de app, e viraria
outra fonte de retaliacao.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

# Abaixo disso o recorte nao vira relatorio: com poucas avaliacoes, "media do
# refeitorio" volta a ser a opiniao identificavel de uma pessoa.
MIN_RATINGS = 5

# A escala e curta de proposito. Quem avalia esta na fila, de bandeja na mao:
# tres opcoes sao um toque, cinco estrelas viram indecisao.
SCALE = {1: "Ruim", 2: "Regular", 3: "Bom"}
BEST = 3

# O que pode ter faltado. Sai do que a operacao resolve: reposicao, quantidade,
# opcao para quem tem restricao, utensilio, bebida.
MISSING_TAGS = {
    "acabou": "Acabou antes de eu chegar",
    "pouca_quantidade": "Pouca quantidade",
    "sem_opcao_restricao": "Nada que eu pudesse comer",
    "faltou_utensilio": "Faltou talher, prato ou copo",
    "faltou_bebida": "Faltou bebida",
    "comida_fria": "Comida fria",
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Rating:
    """Uma avaliacao de um dia. `tags` so faz sentido com `missing` verdadeiro."""

    apetit_unit: str
    service_date: str
    food: int | None = None
    service: int | None = None
    missing: bool = False
    tags: list[str] = field(default_factory=list)
    comment: str = ""
    meal: str = "almoco"

    @property
    def empty(self) -> bool:
        """Avaliacao sem nada dito nao vale gravar."""
        return not any((self.food, self.service, self.missing, self.tags, self.comment.strip()))


def save_rating(conn: sqlite3.Connection, pessoa_id: int, rating: Rating) -> None:
    """Grava a avaliacao do dia. Reavaliar o mesmo dia substitui a anterior."""
    if not rating.apetit_unit.strip():
        raise ValueError("Avaliacao precisa do refeitorio: sem ele nao ha o que agregar.")
    for nota in (rating.food, rating.service):
        if nota is not None and nota not in SCALE:
            raise ValueError(f"Nota fora da escala: {nota}")
    desconhecidas = [t for t in rating.tags if t not in MISSING_TAGS]
    if desconhecidas:
        raise ValueError(f"Motivo de falta desconhecido: {', '.join(desconhecidas)}")

    anterior = conn.execute(
        "SELECT id FROM service_rating WHERE pessoa_id = ? AND service_date = ? AND meal = ?",
        (pessoa_id, rating.service_date, rating.meal),
    ).fetchone()
    if anterior:
        conn.execute("DELETE FROM service_rating_tag WHERE rating_id = ?", (anterior["id"],))
        conn.execute("DELETE FROM service_rating WHERE id = ?", (anterior["id"],))

    # A empresa nao vem de quem chama: vem do cadastro, aqui dentro. Se viesse
    # no `Rating`, a tela poderia mandar qualquer nome e a avaliacao de uma
    # empresa entraria no relatorio de outra. Copiada agora, ela tambem congela
    # o contrato do dia — trocar de empresa nao reescreve o passado.
    cadastro = conn.execute(
        "SELECT client_company FROM employee WHERE pessoa_id = ?", (pessoa_id,)
    ).fetchone()
    empresa = (cadastro["client_company"] if cadastro else "") or ""

    cursor = conn.execute(
        """
        INSERT INTO service_rating (
            pessoa_id, apetit_unit, client_company, service_date, meal,
            food, service, missing_something, comment, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pessoa_id,
            rating.apetit_unit,
            empresa,
            rating.service_date,
            rating.meal,
            rating.food,
            rating.service,
            int(bool(rating.missing)),
            rating.comment.strip(),
            now_iso(),
        ),
    )
    for tag in dict.fromkeys(rating.tags):
        conn.execute(
            "INSERT INTO service_rating_tag (rating_id, tag) VALUES (?, ?)",
            (cursor.lastrowid, tag),
        )
    conn.commit()


def rating_for(
    conn: sqlite3.Connection, pessoa_id: int, service_date: str, meal: str = "almoco"
) -> Rating | None:
    """A avaliacao que a propria pessoa fez naquele dia."""
    row = conn.execute(
        "SELECT * FROM service_rating WHERE pessoa_id = ? AND service_date = ? AND meal = ?",
        (pessoa_id, service_date, meal),
    ).fetchone()
    if not row:
        return None
    tags = [
        linha["tag"]
        for linha in conn.execute(
            "SELECT tag FROM service_rating_tag WHERE rating_id = ? ORDER BY tag", (row["id"],)
        ).fetchall()
    ]
    return Rating(
        apetit_unit=row["apetit_unit"],
        service_date=row["service_date"],
        food=row["food"],
        service=row["service"],
        missing=bool(row["missing_something"]),
        tags=tags,
        comment=row["comment"],
        meal=row["meal"],
    )


def my_ratings(conn: sqlite3.Connection, pessoa_id: int, limit: int = 30) -> list[sqlite3.Row]:
    """O historico de avaliacoes da propria pessoa, para ela ver o que mandou."""
    return conn.execute(
        """
        SELECT service_date, apetit_unit, food, service, missing_something, comment
        FROM service_rating
        WHERE pessoa_id = ?
        ORDER BY service_date DESC
        LIMIT ?
        """,
        (pessoa_id, limit),
    ).fetchall()


def _periodo(from_date: str, to_date: str) -> tuple[str, str]:
    return from_date or "0000-01-01", to_date or "9999-12-31"


# O recorte que a gestao le. Dicionario fechado de proposito: o nome da coluna
# entra numa consulta montada com texto, e so pode sair daqui — nunca de um
# parametro de rota.
DIMENSOES: dict[str, str] = {
    "unidade": "apetit_unit",       # o refeitorio que a Apetit opera
    "empresa": "client_company",    # a empresa cliente: Copel, Sanepar, Coca-Cola
}


def _coluna(dimension: str) -> str:
    try:
        return DIMENSOES[dimension]
    except KeyError:
        raise ValueError(f"Recorte desconhecido: {dimension}") from None


@dataclass
class ServiceReport:
    """Como um recorte do servico foi avaliado num periodo. Nunca carrega pessoa.

    O recorte e a unidade da Apetit ou a empresa cliente. Sao coisas diferentes
    de proposito: uma unidade pode servir mais de uma empresa, e quem tem
    contrato — e quem a Apetit precisa olhar no painel — e a empresa.
    """

    dimension: str
    name: str
    total: int
    food_avg: float | None
    service_avg: float | None
    food_good_pct: float | None
    service_good_pct: float | None
    missing_count: int
    tags: list[tuple[str, int]] = field(default_factory=list)
    suppressed: bool = False
    reason: str = ""
    period_start: str = ""  # preenchido na serie semanal

    @property
    def missing_pct(self) -> float | None:
        if self.suppressed or not self.total:
            return None
        return self.missing_count * 100.0 / self.total


def _report(
    conn: sqlite3.Connection,
    dimension: str,
    name: str,
    from_date: str = "",
    to_date: str = "",
    min_size: int = MIN_RATINGS,
) -> ServiceReport:
    """Resumo de um recorte no periodo, suprimido abaixo do n minimo.

    Este e o **unico** lugar onde a supressao acontece. Unidade e empresa passam
    por aqui porque escrever a regra do n minimo duas vezes e o jeito de as duas
    discordarem um dia — e o lado que discordar para menos publica o numero que
    aponta para uma pessoa.

    Nao ha `pessoa_id` em consulta nenhuma deste arquivo, de proposito: o
    relatorio existe para dizer como o servico esta indo, nao quem disse o que
    sobre ele.
    """
    coluna = _coluna(dimension)
    inicio, fim = _periodo(from_date, to_date)
    linha = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            AVG(food) AS food_avg,
            AVG(service) AS service_avg,
            SUM(CASE WHEN food = ? THEN 1 ELSE 0 END) AS food_good,
            SUM(CASE WHEN service = ? THEN 1 ELSE 0 END) AS service_good,
            SUM(CASE WHEN food IS NOT NULL THEN 1 ELSE 0 END) AS food_n,
            SUM(CASE WHEN service IS NOT NULL THEN 1 ELSE 0 END) AS service_n,
            SUM(missing_something) AS faltas
        FROM service_rating
        WHERE {coluna} = ? AND service_date BETWEEN ? AND ?
        """,
        (BEST, BEST, name, inicio, fim),
    ).fetchone()

    total = linha["total"] or 0
    if total < min_size:
        return ServiceReport(
            dimension=dimension,
            name=name,
            total=total,
            food_avg=None,
            service_avg=None,
            food_good_pct=None,
            service_good_pct=None,
            missing_count=0,
            suppressed=True,
            reason=f"menos de {min_size} avaliacoes no periodo",
        )

    tags = [
        (linha_tag["tag"], linha_tag["total"])
        for linha_tag in conn.execute(
            f"""
            SELECT t.tag, COUNT(*) AS total
            FROM service_rating_tag t
            JOIN service_rating r ON r.id = t.rating_id
            WHERE r.{coluna} = ? AND r.service_date BETWEEN ? AND ?
            GROUP BY t.tag
            ORDER BY total DESC, t.tag
            """,
            (name, inicio, fim),
        ).fetchall()
    ]

    def pct(bons, n) -> float | None:
        return (bons or 0) * 100.0 / n if n else None

    return ServiceReport(
        dimension=dimension,
        name=name,
        total=total,
        food_avg=linha["food_avg"],
        service_avg=linha["service_avg"],
        food_good_pct=pct(linha["food_good"], linha["food_n"]),
        service_good_pct=pct(linha["service_good"], linha["service_n"]),
        missing_count=linha["faltas"] or 0,
        tags=tags,
    )


def _rated(
    conn: sqlite3.Connection, dimension: str, from_date: str = "", to_date: str = ""
) -> list[str]:
    """Os recortes com avaliacao no periodo.

    Linha sem nome fica de fora: avaliacao de quem ainda nao completou o
    cadastro nao tem empresa, e "" nao e uma empresa — viraria uma coluna sem
    dono no painel.
    """
    coluna = _coluna(dimension)
    inicio, fim = _periodo(from_date, to_date)
    return [
        linha[coluna]
        for linha in conn.execute(
            f"""
            SELECT {coluna}, COUNT(*) AS total
            FROM service_rating
            WHERE service_date BETWEEN ? AND ? AND TRIM({coluna}) <> ''
            GROUP BY {coluna}
            ORDER BY total DESC, {coluna}
            """,
            (inicio, fim),
        ).fetchall()
    ]


def _all_reports(
    conn: sqlite3.Connection,
    dimension: str,
    from_date: str = "",
    to_date: str = "",
    min_size: int = MIN_RATINGS,
) -> list[ServiceReport]:
    """Todos os recortes avaliados no periodo, o pior primeiro.

    Ordena por quem precisa de atencao, nao por quem esta bem: o relatorio
    existe para achar onde o servico esta ruim.
    """
    relatorios = [
        _report(conn, dimension, nome, from_date, to_date, min_size)
        for nome in _rated(conn, dimension, from_date, to_date)
    ]
    return sorted(
        relatorios,
        key=lambda r: (r.suppressed, r.food_good_pct if r.food_good_pct is not None else 999),
    )


def _comments(
    conn: sqlite3.Connection,
    dimension: str,
    name: str,
    from_date: str = "",
    to_date: str = "",
    min_size: int = MIN_RATINGS,
) -> list[str]:
    """Os comentarios escritos, sem nenhuma identificacao de quem escreveu.

    So saem se o recorte tiver avaliacoes suficientes. Num periodo de tres
    avaliacoes, "a comida estava fria" e um bilhete assinado — e o proximo
    aprende a nao escrever.

    A ordem e alfabetica, nao cronologica: a ordem de chegada, cruzada com quem
    almocou naquele dia, tambem aponta para uma pessoa.
    """
    coluna = _coluna(dimension)
    inicio, fim = _periodo(from_date, to_date)
    total = conn.execute(
        f"SELECT COUNT(*) AS total FROM service_rating WHERE {coluna} = ? AND service_date BETWEEN ? AND ?",
        (name, inicio, fim),
    ).fetchone()["total"]
    if total < min_size:
        return []
    return [
        linha["comment"]
        for linha in conn.execute(
            f"""
            SELECT comment
            FROM service_rating
            WHERE {coluna} = ? AND service_date BETWEEN ? AND ?
              AND TRIM(comment) <> ''
            ORDER BY comment
            """,
            (name, inicio, fim),
        ).fetchall()
    ]


def _trend(
    conn: sqlite3.Connection,
    dimension: str,
    name: str,
    weeks: int = 8,
    today: str = "",
    min_size: int = MIN_RATINGS,
) -> list[ServiceReport]:
    """Semana a semana, para a gestao ver se piorou ou melhorou.

    Uma media do mes esconde a semana em que o refeitorio caiu. O historico so
    serve se der para ver o movimento.
    """
    fim = date.fromisoformat(today) if today else datetime.now(UTC).date()
    inicio_semana = fim - timedelta(days=fim.weekday())
    relatorios = []
    for atras in range(weeks - 1, -1, -1):
        comeco = inicio_semana - timedelta(weeks=atras)
        relatorio = _report(
            conn, dimension, name, comeco.isoformat(),
            (comeco + timedelta(days=6)).isoformat(), min_size,
        )
        relatorio.period_start = comeco.isoformat()
        relatorios.append(relatorio)
    return relatorios


# --- Por unidade da Apetit: o refeitorio que ela opera -----------------------

def unit_report(conn, apetit_unit: str, from_date: str = "", to_date: str = "",
                min_size: int = MIN_RATINGS) -> ServiceReport:
    return _report(conn, "unidade", apetit_unit, from_date, to_date, min_size)


def rated_units(conn, from_date: str = "", to_date: str = "") -> list[str]:
    return _rated(conn, "unidade", from_date, to_date)


def all_unit_reports(conn, from_date: str = "", to_date: str = "",
                     min_size: int = MIN_RATINGS) -> list[ServiceReport]:
    return _all_reports(conn, "unidade", from_date, to_date, min_size)


def unit_comments(conn, apetit_unit: str, from_date: str = "", to_date: str = "",
                  min_size: int = MIN_RATINGS) -> list[str]:
    return _comments(conn, "unidade", apetit_unit, from_date, to_date, min_size)


def unit_trend(conn, apetit_unit: str, weeks: int = 8, today: str = "",
               min_size: int = MIN_RATINGS) -> list[ServiceReport]:
    return _trend(conn, "unidade", apetit_unit, weeks, today, min_size)


# --- Por empresa cliente: quem tem contrato com a Apetit ---------------------

def company_report(conn, client_company: str, from_date: str = "", to_date: str = "",
                   min_size: int = MIN_RATINGS) -> ServiceReport:
    return _report(conn, "empresa", client_company, from_date, to_date, min_size)


def rated_companies(conn, from_date: str = "", to_date: str = "") -> list[str]:
    return _rated(conn, "empresa", from_date, to_date)


def all_company_reports(conn, from_date: str = "", to_date: str = "",
                        min_size: int = MIN_RATINGS) -> list[ServiceReport]:
    return _all_reports(conn, "empresa", from_date, to_date, min_size)


def company_comments(conn, client_company: str, from_date: str = "", to_date: str = "",
                     min_size: int = MIN_RATINGS) -> list[str]:
    return _comments(conn, "empresa", client_company, from_date, to_date, min_size)


def company_trend(conn, client_company: str, weeks: int = 8, today: str = "",
                  min_size: int = MIN_RATINGS) -> list[ServiceReport]:
    return _trend(conn, "empresa", client_company, weeks, today, min_size)
