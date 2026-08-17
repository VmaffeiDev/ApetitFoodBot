"""Recebimento do cardapio da semana: descobrir de que periodo o arquivo e.

A planilha de planejamento traz so o numero do dia ("17"), sem mes nem ano.
Pelo terminal isso vira `--mes 8 --ano 2025` digitado a mao toda semana; e o
tipo de campo que alguem erra em novembro e publica o cardapio da semana no dia
errado.

Este modulo tira o palpite do nome do arquivo — "Cardapio_17_a_2108.xlsx" diz
21/08 — para a pessoa so **confirmar** em vez de digitar. O palpite nunca
publica sozinho: quem confirma e uma pessoa, olhando as datas ja montadas.
Adivinhar em silencio aqui tem o mesmo custo de antes, so que mais escondido.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

# "17_a_2108", "17 a 21-08", "cardapio 17a2108"
FAIXA = re.compile(r"(\d{1,2})\s*[-_ ]*a[-_ ]*\s*(\d{1,2})[-_./]?(\d{2})(?:[-_./]?(\d{2,4}))?", re.IGNORECASE)
# "08_2025", "2025-08", "agosto_2025"
MES_ANO = re.compile(r"(?:^|[^\d])(0?[1-9]|1[0-2])[-_./](20\d{2})(?:[^\d]|$)")
ANO_MES = re.compile(r"(20\d{2})[-_./](0?[1-9]|1[0-2])(?:[^\d]|$)")

MESES_NOME = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


@dataclass
class Period:
    """Mes e ano propostos para a planilha, e de onde o palpite veio."""

    month: int | None = None
    year: int | None = None
    source: str = ""

    @property
    def known(self) -> bool:
        return bool(self.month and self.year)


def infer_period(filename: str, today: date | None = None) -> Period:
    """Le mes e ano do nome do arquivo. Sem achar, propoe a semana que vem.

    A ordem tenta o mais especifico primeiro: uma faixa de dias com mes colado
    ("17_a_2108") e mais confiavel que um mes solto no nome.
    """
    hoje = today or date.today()
    nome = filename or ""

    achado = FAIXA.search(nome)
    if achado:
        mes = int(achado.group(3))
        ano_bruto = achado.group(4)
        if 1 <= mes <= 12:
            ano = _completa_ano(ano_bruto, hoje.year)
            return Period(mes, ano, f"o nome do arquivo ({achado.group(0)})")

    achado = ANO_MES.search(nome)
    if achado:
        return Period(int(achado.group(2)), int(achado.group(1)), "o nome do arquivo")

    achado = MES_ANO.search(nome)
    if achado:
        return Period(int(achado.group(1)), int(achado.group(2)), "o nome do arquivo")

    minusculo = nome.lower()
    for rotulo, numero in MESES_NOME.items():
        if rotulo in minusculo:
            ano = re.search(r"20\d{2}", nome)
            return Period(numero, int(ano.group(0)) if ano else hoje.year, "o mes escrito no nome do arquivo")

    # Cardapio quase sempre chega para a semana que comeca. Serve de proposta,
    # nunca de decisao: a tela mostra as datas montadas para conferencia.
    proxima = hoje + timedelta(days=(7 - hoje.weekday()) % 7 or 7)
    return Period(proxima.month, proxima.year, "a proxima semana (nao achei data no nome do arquivo)")


def _completa_ano(bruto: str | None, ano_atual: int) -> int:
    if not bruto:
        return ano_atual
    valor = int(bruto)
    return valor if valor > 100 else 2000 + valor


@dataclass
class Preview:
    """O que sera publicado, para alguem conferir antes de publicar."""

    unit: str
    entries: int = 0
    dishes: int = 0
    dates: list[str] = field(default_factory=list)
    without_macros: int = 0
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Pronto para publicar.

        Exige refeitorio: o funcionario ve o cardapio filtrado pela unidade
        dele, entao publicar sem unidade e publicar para ninguem — some sem
        erro nenhum, que e a pior forma de falhar.
        """
        return bool(self.entries and self.unit.strip()) and not self.blocking

    @property
    def period_label(self) -> str:
        if not self.dates:
            return ""
        if len(self.dates) == 1:
            return self.dates[0]
        return f"{self.dates[0]} a {self.dates[-1]}"


DIAS_SEMANA = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")


def weekend_warning(dates: list[str]) -> str:
    """Avisa quando o periodo cai em fim de semana.

    Cardapio de refeitorio corporativo e de segunda a sexta. Se as datas
    montadas caem no sabado ou domingo, quase sempre o mes ou o ano do palpite
    esta errado — os dias vem da planilha, entao e o mes/ano que nao bate.

    E o unico jeito de checar o palpite sem outro palpite: o proprio calendario
    denuncia. Fica como aviso e nao como bloqueio, porque existe refeitorio que
    serve no fim de semana.
    """
    fim_de_semana = []
    for iso in dates:
        try:
            dia = date.fromisoformat(iso)
        except ValueError:
            continue
        if dia.weekday() >= 5:
            fim_de_semana.append(f"{DIAS_SEMANA[dia.weekday()]}, {dia.day}")
    if not fim_de_semana:
        return ""
    return (
        f"O periodo cai em fim de semana ({', '.join(fim_de_semana)}). "
        "Confira o mes e o ano — os dias vem da planilha, entao e o mes/ano que pode estar errado."
    )


def build_preview(entries, issues, unit: str) -> Preview:
    """Resume o que a leitura encontrou, sem gravar nada.

    Publicar cardapio de semana errada e visivel para todo o refeitorio, entao
    a confirmacao precisa mostrar periodo e volume — nao so "importar?".
    """
    datas = sorted({e.service_date for e in entries})
    avisos = [i.detail for i in issues if not i.blocking]
    alerta = weekend_warning(datas)
    if alerta:
        avisos.insert(0, alerta)
    return Preview(
        unit=unit,
        entries=len(entries),
        dishes=len({e.item.code for e in entries}),
        dates=datas,
        without_macros=sum(1 for e in entries if e.item.nutrition.kcal is None),
        blocking=[i.detail for i in issues if i.blocking],
        warnings=avisos,
    )
