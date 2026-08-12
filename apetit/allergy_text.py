"""Reconhecimento do que o funcionario escreve sobre a propria alergia.

A pessoa escreve do jeito dela — "alergia a frutos do mar", "nao posso leite",
"intolerante a lactose" — e aqui isso vira codigo de alergenico.

Duas regras seguram a honestidade do resultado:

1. O que nao for reconhecido **nao e descartado**. Fica registrado como termo
   livre e o app avisa que nao consegue conferir aquilo sozinho. Alguem que
   escreve "alergia a legumes" precisa saber que o app nao checa isso, em vez de
   achar que esta protegido.

2. Nada e adivinhado por semelhanca. So casa com sinonimo conhecido, porque
   errar para o lado do "reconheci" e pior que pedir para a pessoa confirmar.
"""

import re
import unicodedata

from .allergens import ALLERGENS

# Sinonimos como as pessoas escrevem de verdade, ja sem acento.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "leite": (
        "leite", "lactose", "laticinio", "laticinios", "derivados de leite", "derivado de leite",
        "queijo", "queijos", "manteiga", "iogurte", "requeijao", "creme de leite", "nata",
        "leite de vaca", "proteina do leite", "aplv",
    ),
    "gluten": (
        "gluten", "trigo", "farinha de trigo", "centeio", "cevada", "aveia", "malte",
        "celiaco", "celiaca", "doenca celiaca",
    ),
    "crustaceos": (
        "crustaceo", "crustaceos", "camarao", "camaroes", "caranguejo", "siri", "lagosta",
        "lagostim",
    ),
    "peixes": (
        "peixe", "peixes", "atum", "sardinha", "salmao", "bacalhau", "tilapia", "merluza",
        "pescado",
    ),
    "ovos": ("ovo", "ovos", "clara de ovo", "gema", "clara", "ovo de galinha"),
    "amendoim": ("amendoim", "pacoca", "pasta de amendoim", "amendoins"),
    "soja": ("soja", "shoyu", "tofu", "molho de soja", "proteina de soja"),
    "castanhas": (
        "castanha", "castanhas", "castanha de caju", "castanha do para", "castanha do brasil",
        "noz", "nozes", "amendoa", "amendoas", "avela", "avelas", "pistache", "macadamia",
        "oleaginosa", "oleaginosas",
    ),
    "latex": ("latex", "borracha natural"),
}

# Expressoes que cobrem mais de um alergenico de uma vez.
GROUPS: dict[str, tuple[str, ...]] = {
    "frutos do mar": ("crustaceos", "peixes"),
    "fruto do mar": ("crustaceos", "peixes"),
    "frutos-do-mar": ("crustaceos", "peixes"),
    "marisco": ("crustaceos", "peixes"),
    "mariscos": ("crustaceos", "peixes"),
}

# Ruido de linguagem natural que aparece em volta do que interessa.
STOPWORDS = {
    "alergia", "alergias", "alergico", "alergica", "intolerancia", "intolerante",
    "a", "ao", "aos", "as", "de", "do", "da", "dos", "das", "e", "com", "sem",
    "nao", "posso", "como", "comer", "tenho", "sou", "muita", "muito", "um", "uma",
    "que", "meu", "minha", "todo", "toda", "tipo", "qualquer",
}

SEPARATORS = re.compile(r"[,;/]|\be\b|\bou\b|\bnem\b|\btambem\b|\+|\n")


def strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def normalize(text: str) -> str:
    limpo = strip_accents(text).lower()
    limpo = re.sub(r"[^a-z0-9\s-]", " ", limpo)
    return re.sub(r"\s+", " ", limpo).strip()


def _clean_term(term: str) -> str:
    palavras = [p for p in normalize(term).split() if p not in STOPWORDS]
    return " ".join(palavras).strip()


def recognize(text: str) -> tuple[list[str], list[str]]:
    """Le o texto livre e devolve (alergenicos reconhecidos, termos nao reconhecidos).

    O termo nao reconhecido volta como a pessoa escreveu, limpo do ruido, para
    ser mostrado de volta a ela e guardado como alerta manual.
    """
    if not text or not text.strip():
        return [], []

    normalizado = normalize(text)
    if not normalizado:
        return [], []

    # Nada a declarar, dito de varias formas.
    if _clean_term(normalizado) in {"", "nenhuma", "nenhum", "nada", "n a", "na"}:
        return [], []

    reconhecidos: list[str] = []
    nao_reconhecidos: list[str] = []

    # Grupos primeiro: "frutos do mar" tem que casar antes de virar dois pedacos.
    restante = normalizado
    for expressao, codigos in GROUPS.items():
        if expressao in restante:
            for codigo in codigos:
                if codigo not in reconhecidos:
                    reconhecidos.append(codigo)
            restante = restante.replace(expressao, " ")

    for pedaco in SEPARATORS.split(restante):
        termo = _clean_term(pedaco)
        if not termo:
            continue
        achados: list[str] = []
        for codigo, sinonimos in SYNONYMS.items():
            if termo in sinonimos:
                achados.append(codigo)
                break
        if not achados:
            # Sinonimo dentro de uma frase maior ("nao posso comer camarao").
            # Coleta todos: um pedaco pode citar mais de um alimento.
            for codigo, sinonimos in SYNONYMS.items():
                if any(re.search(rf"\b{re.escape(s)}\b", termo) for s in sinonimos):
                    achados.append(codigo)
        if achados:
            for codigo in achados:
                if codigo not in reconhecidos:
                    reconhecidos.append(codigo)
        elif termo not in nao_reconhecidos:
            nao_reconhecidos.append(termo)

    return reconhecidos, nao_reconhecidos


def describe(reconhecidos: list[str], nao_reconhecidos: list[str]) -> str:
    """Devolve para a pessoa o que o app entendeu, para ela confirmar."""
    partes: list[str] = []
    if reconhecidos:
        nomes = ", ".join(ALLERGENS[c] for c in reconhecidos)
        partes.append(f"Vou conferir cada prato contra: {nomes}.")
    if nao_reconhecidos:
        termos = ", ".join(nao_reconhecidos)
        partes.append(
            f"Anotei tambem: {termos}. Isso nao esta na lista que a ficha tecnica declara, "
            "entao nao consigo conferir sozinho — vou te lembrar de perguntar no balcao."
        )
    if not partes:
        partes.append("Nao identifiquei nenhuma alergia no que voce escreveu.")
    return " ".join(partes)
