"""Relatorio do piloto: como o app esta sendo usado.

Um piloto responde uma pergunta: **isso funciona na vida real do refeitorio?**
Isso se mede em adesao e uso — quantas pessoas entraram, quantas voltaram,
quais funcoes pegaram, o que elas disseram do refeitorio.

O que este modulo nao faz, e a razao importa:

    o relatorio nao diz o que cada pessoa come, nem qual e a meta dela, nem se
    ela bateu a meta.

Isso nao e cautela excessiva: e o desenho do produto inteiro. Dado alimentar e
meta nutricional sao dado de saude (LGPD art. 5o, II) dentro de uma relacao de
emprego, e o funcionario aceitou o termo justamente porque o app promete que a
empresa nao ve isso sobre ele. Um relatorio que entregasse a meta de cada um
quebraria o consentimento que tornou o piloto possivel — e num grupo de 15
pessoas, qualquer recorte por setor aponta para alguem.

O que o relatorio entrega, e que responde a pergunta do piloto:

- **adesao**: quantos foram convidados, quantos se cadastraram, quantos usaram
- **retencao**: quantos voltaram depois do primeiro dia, quantos dias por pessoa
- **uso por funcao**: o que as pessoas realmente usam do app
- **distribuicao de objetivo**, agregada e suprimida abaixo do n minimo
- **qualidade do dado**: quanto do cardapio chegou sem macro, quanto sem
  alergenico — porque isso limita o que o app consegue entregar
- **avaliacao do refeitorio**, ja agregada e sem autor

Com isso a empresa decide se amplia. Sem isso — e sem expor ninguem.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

# Mesmo n minimo do resto do app. Num piloto de 15 pessoas, recorte menor que
# isso volta a ser individual.
MIN_AGGREGATE = 5


def _periodo(desde: str, ate: str) -> tuple[str, str]:
    return desde or "0000-01-01", ate or "9999-12-31"


@dataclass
class Funcao:
    nome: str
    pessoas: int
    usos: int
    # Funcao cujo uso, sozinho, revela dado de saude sobre quem usou. "Uma
    # pessoa tem ficha nutricional" num piloto de 15 aponta para alguem tanto
    # quanto o nome dela: a empresa sabe quem foi ao nutricionista.
    saude: bool = False

    @property
    def suprimida(self) -> bool:
        return self.saude and 0 < self.pessoas < MIN_AGGREGATE


@dataclass
class PilotReport:
    """Retrato do piloto. Nenhum campo aqui identifica uma pessoa."""

    desde: str
    ate: str
    convidados: int = 0
    cadastrados: int = 0
    ativos: int = 0                 # registraram ao menos uma refeicao
    voltaram: int = 0               # usaram em mais de um dia
    dias_por_pessoa: float = 0.0
    registros: int = 0
    funcoes: list[Funcao] = field(default_factory=list)
    objetivos: list[tuple[str, int | None]] = field(default_factory=list)
    avaliacoes: int = 0
    comida_boa_pct: float | None = None
    atendimento_bom_pct: float | None = None
    itens_cardapio: int = 0
    itens_sem_macro: int = 0
    itens_sem_alergenico: int = 0
    pessoas_com_restricao: int = 0

    @property
    def adesao_pct(self) -> float | None:
        if not self.convidados:
            return None
        return self.cadastrados * 100.0 / self.convidados

    @property
    def ativacao_pct(self) -> float | None:
        """De quem se cadastrou, quantos chegaram a registrar uma refeicao."""
        if not self.cadastrados:
            return None
        return self.ativos * 100.0 / self.cadastrados

    @property
    def retencao_pct(self) -> float | None:
        """De quem usou, quantos voltaram noutro dia. E o numero que importa:
        usar uma vez e curiosidade; voltar e adocao."""
        if not self.ativos:
            return None
        return self.voltaram * 100.0 / self.ativos

    @property
    def cobertura_macro_pct(self) -> float | None:
        if not self.itens_cardapio:
            return None
        return (self.itens_cardapio - self.itens_sem_macro) * 100.0 / self.itens_cardapio

    @property
    def cobertura_alergenico_pct(self) -> float | None:
        if not self.itens_cardapio:
            return None
        return (self.itens_cardapio - self.itens_sem_alergenico) * 100.0 / self.itens_cardapio


def pilot_report(
    conn: sqlite3.Connection,
    desde: str = "",
    ate: str = "",
    convidados: int = 0,
    min_size: int = MIN_AGGREGATE,
) -> PilotReport:
    """Monta o relatorio do periodo.

    `convidados` e informado por quem conduz o piloto (quantas pessoas
    receberam o convite); o app so sabe quem chegou.
    """
    inicio, fim = _periodo(desde, ate)
    rel = PilotReport(desde=inicio, ate=fim, convidados=convidados)

    rel.cadastrados = conn.execute(
        "SELECT COUNT(*) AS t FROM employee WHERE consent_accepted = 1"
    ).fetchone()["t"]

    uso = conn.execute(
        """
        SELECT telegram_id, COUNT(DISTINCT service_date) AS dias, SUM(quantity) AS itens
        FROM consumption
        WHERE service_date BETWEEN ? AND ?
        GROUP BY telegram_id
        """,
        (inicio, fim),
    ).fetchall()
    rel.ativos = len(uso)
    rel.voltaram = sum(1 for linha in uso if linha["dias"] > 1)
    rel.registros = sum(linha["dias"] for linha in uso)
    rel.dias_por_pessoa = (rel.registros / rel.ativos) if rel.ativos else 0.0

    rel.funcoes = _uso_por_funcao(conn, inicio, fim)
    rel.objetivos = _objetivos(conn, min_size)

    aval = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN food = 3 THEN 1 ELSE 0 END) AS comida_boa,
               SUM(CASE WHEN food IS NOT NULL THEN 1 ELSE 0 END) AS comida_n,
               SUM(CASE WHEN service = 3 THEN 1 ELSE 0 END) AS atend_bom,
               SUM(CASE WHEN service IS NOT NULL THEN 1 ELSE 0 END) AS atend_n
        FROM service_rating WHERE service_date BETWEEN ? AND ?
        """,
        (inicio, fim),
    ).fetchone()
    rel.avaliacoes = aval["total"] or 0
    if rel.avaliacoes >= min_size:
        if aval["comida_n"]:
            rel.comida_boa_pct = (aval["comida_boa"] or 0) * 100.0 / aval["comida_n"]
        if aval["atend_n"]:
            rel.atendimento_bom_pct = (aval["atend_bom"] or 0) * 100.0 / aval["atend_n"]

    rel.itens_cardapio, rel.itens_sem_macro, rel.itens_sem_alergenico = _qualidade_do_dado(conn)
    rel.pessoas_com_restricao = conn.execute(
        """
        SELECT COUNT(DISTINCT telegram_id) AS t FROM (
            SELECT telegram_id FROM employee_restriction
            UNION SELECT telegram_id FROM employee_free_restriction
        )
        """
    ).fetchone()["t"]
    return rel


# Cada funcao do app e medida pela marca que ela deixa no banco. Nao ha log de
# tela: contar tela vista exigiria registrar o passo a passo de cada pessoa, o
# que e exatamente o tipo de rastro que este app evita.
def _uso_por_funcao(conn: sqlite3.Connection, inicio: str, fim: str) -> list[Funcao]:
    consultas = [
        ("Registrar a refeição",
         "SELECT COUNT(DISTINCT telegram_id) p, COUNT(*) u FROM consumption WHERE service_date BETWEEN ? AND ?",
         (inicio, fim)),
        ("Seguir a sugestão de porção",
         "SELECT COUNT(DISTINCT telegram_id) p, COUNT(*) u FROM consumption "
         "WHERE source = 'sugestao' AND service_date BETWEEN ? AND ?",
         (inicio, fim)),
        ("Avaliar o refeitório",
         "SELECT COUNT(DISTINCT telegram_id) p, COUNT(*) u FROM service_rating WHERE service_date BETWEEN ? AND ?",
         (inicio, fim)),
        # `created_at`/`updated_at` sao timestamp ISO completo; o corte usa so a
        # data para o periodo pedido valer igual ao das consultas de cima.
        ("Guardar favorito",
         "SELECT COUNT(DISTINCT telegram_id) p, COUNT(*) u FROM favorite "
         "WHERE substr(created_at, 1, 10) BETWEEN ? AND ?",
         (inicio, fim)),
        ("Ficha nutricional",
         "SELECT COUNT(DISTINCT telegram_id) p, COUNT(*) u FROM employee_prescription "
         "WHERE substr(updated_at, 1, 10) BETWEEN ? AND ?",
         (inicio, fim)),
    ]
    saude = {"Ficha nutricional"}
    funcoes = []
    for nome, sql, params in consultas:
        try:
            linha = conn.execute(sql, params).fetchone()
        except sqlite3.OperationalError:  # tabela de versao mais nova
            continue
        funcoes.append(Funcao(nome, linha["p"] or 0, linha["u"] or 0, saude=nome in saude))
    return sorted(funcoes, key=lambda f: f.pessoas, reverse=True)


def _objetivos(conn: sqlite3.Connection, min_size: int) -> list[tuple[str, int | None]]:
    """Distribuicao de objetivo, suprimida abaixo do n minimo.

    Objetivo e escolha de saude. Sai so como distribuicao do grupo, e so
    quando o grupo e grande o bastante para a contagem nao apontar para uma
    pessoa — num piloto de 15, "1 pessoa quer emagrecer" identifica alguem.
    """
    linhas = conn.execute(
        "SELECT goal, COUNT(*) AS t FROM employee WHERE consent_accepted = 1 "
        "AND TRIM(goal) <> '' GROUP BY goal ORDER BY t DESC, goal"
    ).fetchall()
    return [(l["goal"], l["t"] if l["t"] >= min_size else None) for l in linhas]


def _qualidade_do_dado(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Quanto do cardapio esta completo. Limita o que o app consegue entregar."""
    total = conn.execute("SELECT COUNT(*) AS t FROM menu_item").fetchone()["t"]
    sem_macro = conn.execute(
        "SELECT COUNT(*) AS t FROM menu_item WHERE kcal IS NULL OR ptn_g IS NULL"
    ).fetchone()["t"]
    sem_alergenico = conn.execute(
        "SELECT COUNT(*) AS t FROM menu_item i WHERE NOT EXISTS "
        "(SELECT 1 FROM menu_item_allergen a WHERE a.item_code = i.code)"
    ).fetchone()["t"]
    return total, sem_macro, sem_alergenico


def daily_activity(conn: sqlite3.Connection, desde: str = "", ate: str = "") -> list[tuple[str, int]]:
    """Pessoas distintas por dia. E a curva que mostra se o piloto pegou."""
    inicio, fim = _periodo(desde, ate)
    return [
        (l["service_date"], l["pessoas"])
        for l in conn.execute(
            "SELECT service_date, COUNT(DISTINCT telegram_id) AS pessoas FROM consumption "
            "WHERE service_date BETWEEN ? AND ? GROUP BY service_date ORDER BY service_date",
            (inicio, fim),
        ).fetchall()
    ]


def weeks_in(desde: str, ate: str) -> int:
    try:
        dias = (date.fromisoformat(ate) - date.fromisoformat(desde)).days
    except ValueError:
        return 0
    return max(1, round((dias + 1) / 7))


def format_report(rel: PilotReport) -> list[str]:
    """O relatorio em texto, do jeito que sai no bot e vai para a empresa."""
    pct = lambda v: "—" if v is None else f"{v:.0f}%"
    linhas = [
        "\U0001f4ca RELATORIO DO PILOTO",
        f"{rel.desde} a {rel.ate}",
        "",
        "ADESAO",
    ]
    if rel.convidados:
        linhas.append(f"  Convidados:        {rel.convidados}")
    linhas += [
        f"  Cadastrados:       {rel.cadastrados}" + (f"  ({pct(rel.adesao_pct)} dos convidados)" if rel.convidados else ""),
        f"  Usaram de fato:    {rel.ativos}  ({pct(rel.ativacao_pct)} de quem se cadastrou)",
        f"  Voltaram outro dia:{rel.voltaram:>3}  ({pct(rel.retencao_pct)} de quem usou)",
        "",
        "USO",
        f"  Refeicoes registradas: {rel.registros}",
        f"  Dias por pessoa:       {rel.dias_por_pessoa:.1f}",
        "",
        "O QUE AS PESSOAS USAM",
    ]
    for f in rel.funcoes:
        if f.suprimida:
            linhas.append(f"  {f.nome:<28} suprimido (grupo pequeno)")
        else:
            linhas.append(f"  {f.nome:<28} {f.pessoas} pessoa(s), {f.usos} vez(es)")

    linhas += ["", "OBJETIVO DECLARADO (agregado)"]
    for objetivo, total in rel.objetivos:
        linhas.append(f"  {objetivo:<28} " + (f"{total} pessoa(s)" if total is not None else "suprimido (grupo pequeno)"))

    linhas += [
        "",
        "AVALIACAO DO REFEITORIO",
        f"  Avaliacoes:        {rel.avaliacoes}",
        f"  Comida boa:        {pct(rel.comida_boa_pct)}",
        f"  Atendimento bom:   {pct(rel.atendimento_bom_pct)}",
        "",
        "QUALIDADE DO DADO DO CARDAPIO",
        f"  Itens no cardapio:      {rel.itens_cardapio}",
        f"  Com informacao nutricional: {pct(rel.cobertura_macro_pct)}",
        f"  Com alergenico declarado:   {pct(rel.cobertura_alergenico_pct)}",
        f"  Pessoas com restricao declarada: {rel.pessoas_com_restricao}",
        "",
        "O QUE NAO ENTRA NESTE RELATORIO",
        "  O que cada pessoa comeu, a meta de cada pessoa e se ela bateu a meta.",
        "  Dado alimentar e dado de saude dentro de relacao de emprego, e o",
        "  funcionario aceitou o termo porque o app promete que a empresa nao ve",
        "  isso sobre ele. Num grupo pequeno, qualquer recorte aponta para alguem.",
    ]
    return linhas
