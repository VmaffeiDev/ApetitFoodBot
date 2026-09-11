"""Alergenico deduzido da lista de ingredientes da receita.

O `apetit/allergens.py` abre dizendo qual e o erro que machuca: deduzir
alergenico do **nome** do prato. "STROGONOFF DE CARNE" nao avisa que leva creme
de leite; "FILE DE FRANGO A MILANESA" nao avisa que leva ovo e trigo. Por isso
o app respondia "nao sei" para quase todo prato — 0% de cobertura.

A planilha de receitas resolve isso pela raiz: ela traz **a lista de
ingredientes de cada prato**. E deduzir do ingrediente e outra coisa —
"FARINHA DE TRIGO BRANCA" na receita da milanesa nao e palpite sobre o nome, e
o que a cozinha poe na panela.

Tres regras seguram a deducao:

**1. Ingrediente prova presenca, nunca ausencia.** Achar leite na lista prova
que o prato tem leite. *Nao* achar nao prova nada: a receita pode omitir o
molho pronto, o ingrediente composto pode esconder alergenico na formula dele,
e existe contaminacao cruzada na cozinha, que nenhuma lista de ingredientes
enxerga. Por isso este modulo **nunca emite `nao_contem`** — so `contem` e
`pode_conter`. Quem libera um prato e a nutricionista, olhando a ficha.

**2. Casamento e por palavra inteira, com excecao explicita.** "COUVE
MANTEIGA" nao tem manteiga, "PAO SIRIO" nao tem siri, "PAO DE QUEIJO" e de
polvilho e nao tem gluten, "LEITE DE COCO" nao e leite e "NOZ MOSCADA" nao e
noz. Busca por pedaco de palavra erraria em todos esses — e um ⚠️ falso ensina
a pessoa a ignorar o ⚠️ verdadeiro.

**3. Prato ambiguo fica sem declaracao.** "CARNE ASSADA AO MOLHO" existe como
duas receitas: com champignon e ao molho madeira, com ingredientes diferentes.
Escolher uma seria atribuir ao prato a lista de ingredientes de outro. Quando
as variantes **concordam** sobre um alergenico, a resposta e a mesma qualquer
que seja a variante e da para declarar; onde discordam, fica sem declaracao.

O resultado sai marcado como deduzido da lista de ingredientes, nao como
declaracao da ficha tecnica: e insumo para a nutricionista revisar, e a fila de
revisao existe para isso.
"""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from .allergens import ALLERGENS, Declaration

# Oleo de soja aparece em 1.811 das 27.298 linhas: quase todo prato salgado
# leva. A RDC 26/2015 dispensa de declaracao os oleos vegetais totalmente
# refinados, porque o refino tira a proteina que causa a reacao — e marcar
# "contem soja" em todo prato apagaria o aviso para quem tem alergia de
# verdade, que e justamente quem precisa dele.
#
# Como o refino do oleo usado aqui nao esta na planilha, o app nao afirma:
# marca `pode_conter` e deixa visivel. Uma linha so para a nutricionista mudar
# se ela confirmar que o caso e outro.
OLEO_VEGETAL_REFINADO = Declaration.PODE_CONTER


def normalize(texto: str) -> str:
    """Sem acento, caixa alta, so letra e numero — o formato que os padroes esperam."""
    sem_acento = unicodedata.normalize("NFD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]+", " ", sem_acento)).strip().upper()


# Ingredientes que parecem carregar um alergenico e nao carregam. Conferidos um
# a um contra a planilha real; cada linha aqui e um ⚠️ falso que o app nao vai
# mostrar.
NAO_E_ALERGENICO = (
    (r"\bCOUVE (MANTEIGA|FLOR)\b", "leite"),        # hortalica, nao laticinio
    (r"\bPAO DE QUEIJO\b", "gluten"),               # polvilho: nao leva trigo
    (r"\bFERMENTO EM PO\b", "gluten"),              # amido de milho
    (r"\bFERMENTO BIOLOGICO\b", "gluten"),          # levedura pura
    (r"\bLEITE DE (COCO|SOJA|AMENDOA|CASTANHA|AVEIA)\b", "leite"),
    (r"\bIOGURTE DE COCO\b", "leite"),
    (r"\bNOZ MOSCADA\b|\bNOZ-MOSCADA\b", "castanhas"),  # especiaria
    (r"\bPAO SIRIO\b|\bPIMENTA SIRIA\b", "crustaceos"),
    (r"\bOVOMALTINE\b", "ovos"),                    # marca, nao ovo
    (r"\bPALITO DE SORVETE\b|\bCANUDO BIJU\b|\bCASQUINHA\b", "leite"),  # acessorio
    (r"\bSORVETE MASSA\b|\bSORVETE\b", "gluten"),   # "massa" de sorvete nao e macarrao
    (r"\bFARINHA DE MANDIOCA\b|\bFARINHA DE MILHO\b|\bFARINHA DE AVEIA\b", "gluten"),
    (r"\bAMIDO DE MILHO\b|\bPOLVILHO\b", "gluten"),
)

# Ingrediente que **contem** o alergenico, sem duvida razoavel.
CONTEM = (
    # leite e derivados
    (r"\bLEITE\b", "leite"),
    (r"\bLACTEO\b|\bLACTEA\b|\bLACTOSE\b", "leite"),
    (r"\bQUEIJO\b|\bMUSSARELA\b|\bPARMESAO\b|\bRICOTA\b|\bCATUPIRY\b|\bCHEDDAR\b", "leite"),
    (r"\bREQUEIJAO\b|\bIOGURTE\b|\bCHANTILLY\b|\bMANTEIGA\b|\bNATA\b|\bCOALHO\b", "leite"),
    (r"\bSORVETE\b|\bCHURROS\b", "leite"),
    # ovos
    (r"\bOVO\b|\bOVOS\b|\bCLARA\b|\bGEMA\b", "ovos"),
    (r"\bMAIONESE\b", "ovos"),
    # gluten
    (r"\bTRIGO\b|\bFARINHA DE ROSCA\b|\bSEMOLA\b|\bGLUTEN\b", "gluten"),
    (r"\bMACARRAO\b|\bMASSA\b|\bNHOQUE\b|\bLASANHA\b|\bRAVIOLI\b|\bCAPPELLETTI\b|\bRONDELE\b|\bCANELONE\b|\bCONCHIGLIONI\b", "gluten"),
    (r"\bPAO\b|\bPAES\b|\bBISCOITO\b|\bTORRADA\b|\bROSCA\b|\bBOLACHA\b|\bWAFER\b|\bPIZZA\b", "gluten"),
    (r"\bAVEIA\b|\bCEVADA\b|\bCENTEIO\b|\bCERVEJA\b|\bMALTE\b|\bKIBE\b", "gluten"),
    (r"\bSHOYU\b", "gluten"),                       # shoyu leva trigo na fermentacao
    (r"\bMISTURA P BOLO\b|\bMISTURA P BROWNIE\b|\bFAROFA\b", "gluten"),
    # soja — o oleo refinado fica de fora deste `contem` de proposito (ver
    # OLEO_VEGETAL_REFINADO no topo do modulo); o resto da soja entra.
    (r"\bSHOYU\b|\bMISSO\b|\bTOFU\b", "soja"),
    (r"(?<!OLEO DE )\bSOJA\b", "soja"),
    # peixes e crustaceos
    (r"\bPEIXE\b|\bMERLUZA\b|\bSARDINHA\b|\bATUM\b|\bBACALHAU\b|\bSALMAO\b|\bPESCADA\b|\bTILAPIA\b|\bTAMBATINGA\b|\bANCHOVA\b", "peixes"),
    (r"\bCAMARAO\b|\bLAGOSTA\b|\bCARANGUEJO\b|\bSIRI\b", "crustaceos"),
    # amendoim e castanhas
    (r"\bAMENDOIM\b|\bPACOCA\b", "amendoim"),
    (r"\bCASTANHA\b|\bNOZES\b|\bAMENDOA\b|\bAMENDOAS\b|\bAVELA\b|\bPISTACHE\b", "castanhas"),
)

# Ingrediente industrializado de formula que a planilha nao abre. Nao da para
# afirmar que contem, nem que nao contem — e exatamente o que `pode_conter`
# existe para dizer.
PODE_CONTER = (
    (r"\bCALDO DE\b|\bTEMPERO\b|\bMISTURA P\b|\bPREPARADO\b|\bPO P\b|\bDESIDRATAD", ("gluten", "soja", "leite")),
    (r"\bMOLHO\b", ("gluten", "soja", "leite")),
    (r"\bMARGARINA\b", ("leite",)),                 # muitas levam soro de leite
    (r"\bCHOCOLATE\b|\bACHOCOLATADO\b|\bCACAU\b|\bBOMBOM\b|\bBARRA DE CEREAL\b|\bBARRA CER\b", ("leite", "castanhas", "amendoim")),
    (r"\bSALGADO\b|\bTORTA\b|\bBOLO\b|\bEMPANAD|\bNUGGET|\bFOLHADO\b|\bCROISSANT\b|\bCOXINHA\b", ("gluten", "leite", "ovos")),
    (r"\bOLEO DE SOJA\b", ("soja",)),   # ver OLEO_VEGETAL_REFINADO no topo
)

# Molho de tomate e de pimenta sao simples o bastante para nao arrastarem o
# `pode_conter` largo de "molho pronto" — mas so na forma liquida. "MOLHO DE
# TOMATE EM PO" e preparado desidratado, com a formula fechada de sempre, entao
# continua caindo no `pode_conter`.
MOLHO_SIMPLES = re.compile(r"\bMOLHO DE (TOMATE|PIMENTA)\b|\bTOMATE MOLHO\b")
INDUSTRIALIZADO = re.compile(r"\bEM PO\b|\bDESIDRATAD|\bINSTANTANE")
REGRA_MOLHO = r"\bMOLHO\b"


@dataclass
class Recipe:
    """Uma receita da planilha: o prato e o que entra nele."""

    code: str
    name: str
    group: str = ""
    ingredients: list[str] = field(default_factory=list)


def read_recipe_rows(rows) -> dict[str, Recipe]:
    """Le as linhas da aba Receitas e agrupa por prato.

    Layout: ParentKey, Nome da Receita, Codigo do Grupo, Nome do Grupo,
    LineNum, ItemCode, Descricao do item, Quantidade, Warehouse — uma linha por
    ingrediente.
    """
    receitas: dict[str, Recipe] = {}
    for linha in rows:
        if not linha or not linha[0] or str(linha[0]).strip().lower() == "parentkey":
            continue
        codigo = str(linha[0]).strip()
        receita = receitas.get(codigo)
        if receita is None:
            receita = Recipe(
                code=codigo,
                name=str(linha[1] or "").strip(),
                group=str(linha[3] or "").strip() if len(linha) > 3 else "",
            )
            receitas[codigo] = receita
        ingrediente = str(linha[6] or "").strip() if len(linha) > 6 else ""
        if ingrediente:
            receita.ingredients.append(ingrediente)
    return receitas


def allergens_for_ingredient(ingrediente: str) -> dict[str, Declaration]:
    """O que um ingrediente sozinho diz. Nunca devolve `nao_contem`."""
    texto = normalize(ingrediente)
    if not texto:
        return {}

    # A excecao vem primeiro: ela existe para desarmar um padrao que casaria.
    bloqueados = {alerg for padrao, alerg in NAO_E_ALERGENICO if re.search(padrao, texto)}

    achados: dict[str, Declaration] = {}
    for padrao, alergs in PODE_CONTER:
        if padrao == REGRA_MOLHO and MOLHO_SIMPLES.search(texto) and not INDUSTRIALIZADO.search(texto):
            continue
        if re.search(padrao, texto):
            for alerg in alergs:
                if alerg not in bloqueados:
                    achados.setdefault(alerg, Declaration.PODE_CONTER)

    for padrao, alerg in CONTEM:
        if alerg in bloqueados:
            continue
        if re.search(padrao, texto):
            # `contem` e mais forte que `pode_conter` e sobrescreve.
            achados[alerg] = Declaration.CONTEM
    return achados


def allergens_for_recipe(ingredientes) -> dict[str, Declaration]:
    """O que a receita inteira diz. Ausencia de ingrediente nao vira `nao_contem`."""
    total: dict[str, Declaration] = {}
    for ingrediente in ingredientes:
        for alerg, decl in allergens_for_ingredient(ingrediente).items():
            if total.get(alerg) is not Declaration.CONTEM:
                total[alerg] = decl
    return {a: d for a, d in total.items() if a in ALLERGENS}


@dataclass
class Match:
    """Uma receita ligada a um prato do catalogo, e por qual caminho."""

    item_code: str
    declarations: dict[str, Declaration]
    recipes: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return len(self.recipes) > 1


def declarations_by_item(
    receitas: dict[str, Recipe],
    slugify,
) -> dict[str, Match]:
    """Agrupa receitas por prato do catalogo e resolve variantes.

    Um prato do cardapio pode ter varias receitas ("OVO FRITO - 1", "- 2",
    "- 3"), e nomes que so diferem no sufixo caem no mesmo slug. Quando as
    variantes concordam sobre um alergenico, a resposta e a mesma qualquer que
    seja a que foi para a panela, e da para declarar. Onde discordam, o prato
    fica **sem declaracao** naquele alergenico: declarar o de uma variante seria
    atribuir ao prato o ingrediente de outro.
    """
    por_slug: dict[str, list[Recipe]] = defaultdict(list)
    for receita in receitas.values():
        if receita.name:
            por_slug[slugify(receita.name)].append(receita)

    resultado: dict[str, Match] = {}
    for slug, grupo in por_slug.items():
        leituras = [allergens_for_recipe(r.ingredients) for r in grupo]
        acordo: dict[str, Declaration] = {}
        conflito: list[str] = []
        for alerg in {a for leitura in leituras for a in leitura}:
            valores = {leitura.get(alerg) for leitura in leituras}
            if len(valores) == 1:
                acordo[alerg] = valores.pop()
            else:
                conflito.append(alerg)
        resultado[slug] = Match(
            item_code=slug,
            declarations=acordo,
            recipes=[r.code for r in grupo],
            conflicting=sorted(conflito),
        )
    return resultado
