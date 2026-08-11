"""Alergenicos declarados por prato e conferencia contra o cadastro do funcionario.

Regra central deste modulo: o sistema **nunca afirma que um prato e seguro sem
dado declarado**. Por isso a conferencia tem tres estados, nao dois.

Deduzir alergenico do nome do prato e o erro que machuca: "STROGONOFF DE CARNE"
nao avisa que leva creme de leite, "FILE DE FRANGO A MILANESA" nao avisa que
leva ovo e trigo. Quando o alergenico do funcionario nao foi declarado para
aquele prato, a resposta e "nao sei", e quem confirma e a pessoa no balcao.

A lista segue os alergenicos de declaracao obrigatoria da RDC 26/2015 da ANVISA.
"""

from dataclasses import dataclass, field
from enum import Enum

# Codigo -> rotulo exibido ao funcionario.
ALLERGENS: dict[str, str] = {
    "gluten": "Gluten (trigo, centeio, cevada, aveia)",
    "crustaceos": "Crustaceos",
    "ovos": "Ovos",
    "peixes": "Peixes",
    "amendoim": "Amendoim",
    "soja": "Soja",
    "leite": "Leite e derivados",
    "castanhas": "Castanhas, nozes, amendoas e pistache",
    "latex": "Latex natural",
}


class Declaration(str, Enum):
    """O que a ficha tecnica diz sobre um alergenico naquele prato."""

    CONTEM = "contem"
    PODE_CONTER = "pode_conter"
    NAO_CONTEM = "nao_contem"
    NAO_DECLARADO = "nao_declarado"


class Verdict(str, Enum):
    """O que o app responde ao funcionario."""

    BLOQUEIO = "bloqueio"        # contem alergenico que a pessoa declarou
    ATENCAO = "atencao"          # pode conter, ou ninguem declarou: nao da para afirmar
    LIBERADO = "liberado"        # todos os alergenicos da pessoa constam como nao contem
    SEM_RESTRICAO = "sem_restricao"


class RestrictionKind(str, Enum):
    ALERGIA = "alergia"
    INTOLERANCIA = "intolerancia"
    PREFERENCIA = "preferencia"


@dataclass(frozen=True)
class Restriction:
    allergen: str
    kind: RestrictionKind = RestrictionKind.ALERGIA


@dataclass
class SafetyCheck:
    verdict: Verdict
    contem: list[str] = field(default_factory=list)
    pode_conter: list[str] = field(default_factory=list)
    nao_declarado: list[str] = field(default_factory=list)

    @property
    def safe_to_affirm(self) -> bool:
        """So e verdade quando toda restricao da pessoa foi explicitamente descartada."""
        return self.verdict in (Verdict.LIBERADO, Verdict.SEM_RESTRICAO)

    def message(self) -> str:
        rotulo = lambda codes: ", ".join(ALLERGENS.get(c, c) for c in codes)
        if self.verdict is Verdict.SEM_RESTRICAO:
            return "Voce nao tem restricao cadastrada."
        if self.verdict is Verdict.BLOQUEIO:
            return (
                f"Este prato contem {rotulo(self.contem)}, que esta na sua ficha. "
                "Nao recomendo pedir."
            )
        if self.verdict is Verdict.ATENCAO:
            partes = []
            if self.pode_conter:
                partes.append(f"pode conter {rotulo(self.pode_conter)}")
            if self.nao_declarado:
                partes.append(f"nao tem declaracao sobre {rotulo(self.nao_declarado)}")
            return (
                "Nao consigo confirmar que este prato e seguro para voce: "
                + " e ".join(partes)
                + ". Confirme no balcao antes de se servir."
            )
        return "Nenhum alergenico da sua ficha aparece neste prato."


def check_item(
    restrictions: list[Restriction],
    declared: dict[str, Declaration | str],
) -> SafetyCheck:
    """Cruza as restricoes da pessoa com o que a ficha do prato declara.

    `declared` mapeia codigo de alergenico para a declaracao daquele prato.
    Alergenico ausente do dicionario conta como NAO_DECLARADO de proposito:
    a falta de informacao nunca vira liberacao.
    """
    if not restrictions:
        return SafetyCheck(Verdict.SEM_RESTRICAO)

    contem: list[str] = []
    pode_conter: list[str] = []
    nao_declarado: list[str] = []

    for restriction in restrictions:
        bruto = declared.get(restriction.allergen, Declaration.NAO_DECLARADO)
        estado = Declaration(bruto)
        if estado is Declaration.CONTEM:
            contem.append(restriction.allergen)
        elif estado is Declaration.PODE_CONTER:
            pode_conter.append(restriction.allergen)
        elif estado is Declaration.NAO_DECLARADO:
            nao_declarado.append(restriction.allergen)

    if contem:
        verdict = Verdict.BLOQUEIO
    elif pode_conter or nao_declarado:
        verdict = Verdict.ATENCAO
    else:
        verdict = Verdict.LIBERADO

    return SafetyCheck(verdict, contem, pode_conter, nao_declarado)


def coverage(declared: dict[str, Declaration | str]) -> float:
    """Fracao dos alergenicos obrigatorios que a ficha do prato declara.

    Serve de metrica para o nutricionista: cardapio com cobertura baixa nao
    sustenta a funcao de alerta, por mais bonita que a tela fique.
    """
    if not ALLERGENS:
        return 0.0
    declarados = sum(
        1
        for code in ALLERGENS
        if Declaration(declared.get(code, Declaration.NAO_DECLARADO)) is not Declaration.NAO_DECLARADO
    )
    return declarados / len(ALLERGENS)
