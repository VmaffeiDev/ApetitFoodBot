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
    # Termos que a pessoa escreveu e o app nao sabe conferir, por nao existirem
    # como campo na ficha tecnica (ex.: "legumes", "pimenta").
    nao_verificavel: list[str] = field(default_factory=list)
    # Termos que aparecem no proprio nome do prato ("feijao" em "FEIJAO PRETO").
    # O cardapio diz isso em voz alta, entao da para bloquear com confianca.
    no_nome: list[str] = field(default_factory=list)

    @property
    def safe_to_affirm(self) -> bool:
        """So e verdade quando toda restricao da pessoa foi explicitamente descartada."""
        return self.verdict in (Verdict.LIBERADO, Verdict.SEM_RESTRICAO)

    def message(self) -> str:
        rotulo = lambda codes: ", ".join(ALLERGENS.get(c, c) for c in codes)
        if self.verdict is Verdict.SEM_RESTRICAO:
            return "Voce nao tem restricao cadastrada."
        if self.verdict is Verdict.BLOQUEIO:
            motivos = []
            if self.contem:
                motivos.append(f"contem {rotulo(self.contem)}")
            if self.no_nome:
                motivos.append(f"o proprio nome indica {', '.join(self.no_nome)}")
            return "Este prato " + " e ".join(motivos) + ", que voce evita. Nao recomendo pedir."
        if self.verdict is Verdict.ATENCAO:
            partes = []
            if self.pode_conter:
                partes.append(f"pode conter {rotulo(self.pode_conter)}")
            if self.nao_declarado:
                partes.append(f"nao tem declaracao sobre {rotulo(self.nao_declarado)}")
            if self.nao_verificavel:
                partes.append(f"nao consigo checar {', '.join(self.nao_verificavel)}")
            return (
                "Nao consigo confirmar que este prato e seguro para voce: "
                + " e ".join(partes)
                + ". Confirme no balcao antes de se servir."
            )
        return "Nenhum alergenico da sua ficha aparece neste prato."


def check_item(
    restrictions: list[Restriction],
    declared: dict[str, Declaration | str],
    unverifiable: list[str] | tuple[str, ...] = (),
    dish_text: str = "",
    avoid_terms: list[str] | tuple[str, ...] = (),
) -> SafetyCheck:
    """Cruza as restricoes da pessoa com o que a ficha do prato declara.

    `declared` mapeia codigo de alergenico para a declaracao daquele prato.
    Alergenico ausente do dicionario conta como NAO_DECLARADO de proposito:
    a falta de informacao nunca vira liberacao.

    `unverifiable` sao termos que a pessoa escreveu e que nao existem como campo
    em ficha tecnica ("legumes", "pimenta"). Enquanto houver um deles, o prato
    nunca sai como liberado — mostrar visto verde a quem tem restricao que o app
    nao checa e pior do que nao mostrar nada.

    `dish_text` e o nome do prato. Quando um desses termos aparece ali
    ("feijao" em "FEIJAO PRETO"), da para bloquear com confianca, porque o
    cardapio esta dizendo em voz alta. O contrario nao vale: nao achar o nome
    nao prova ausencia, entao isso nunca libera.

    `avoid_terms` sao alimentos que a pessoa so prefere evitar, sem risco de
    saude. A diferenca esta no silencio: eles bloqueiam quando aparecem no nome
    do prato, mas nao enchem o resto do cardapio de aviso. Tratar preferencia
    com o rigor de alergia gera tanto alerta que a pessoa para de ler o que
    importa.
    """
    from .allergy_text import terms_in_dish  # import local: allergy_text importa este modulo

    nao_verificavel = list(unverifiable)
    evitar = list(avoid_terms)
    no_nome: list[str] = []
    if dish_text:
        no_nome = terms_in_dish(dish_text, nao_verificavel + evitar)
        nao_verificavel = [t for t in nao_verificavel if t not in no_nome]

    if not restrictions:
        if no_nome:
            return SafetyCheck(Verdict.BLOQUEIO, nao_verificavel=nao_verificavel, no_nome=no_nome)
        if nao_verificavel:
            return SafetyCheck(Verdict.ATENCAO, nao_verificavel=nao_verificavel)
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

    if contem or no_nome:
        verdict = Verdict.BLOQUEIO
    elif pode_conter or nao_declarado or nao_verificavel:
        verdict = Verdict.ATENCAO
    else:
        verdict = Verdict.LIBERADO

    return SafetyCheck(verdict, contem, pode_conter, nao_declarado, nao_verificavel, no_nome)


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
