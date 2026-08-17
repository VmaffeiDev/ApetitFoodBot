"""Traducao de numero para linguagem que o funcionario entende.

O app e para controle proprio de quem quer comer melhor, nao para quem sabe ler
macronutriente. "134 kcal, 12 g PTN" e dado; "prato leve, boa fonte de proteina"
e informacao. Este modulo faz essa ponte, num lugar so, para a mesma leitura
valer no bot e em qualquer tela futura.
"""

from datetime import date

DIAS = ("segunda-feira", "terca-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sabado", "domingo")
MESES = (
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)

# Ordem em que a pessoa anda na fila do refeitorio. E a ordem em que o app
# pergunta, para o app acompanhar a bandeja em vez de contrariar.
SERVING_ORDER = (
    "PRATO PRINCIPAL",
    "OPCAO AO PP",
    "GUARNICAO",
    "ARROZ",
    "FEIJAO",
    "SALADA",
    "FRUTA",
    "SOBREMESA",
    "BEBIDA",
    "LANCHE",
    "ACOMPANHAMENTO",
)

CATEGORY_LABEL = {
    "PRATO PRINCIPAL": "Prato principal",
    "OPCAO AO PP": "Opcao ao prato principal",
    "GUARNICAO": "Guarnicao",
    "ARROZ": "Arroz",
    "FEIJAO": "Feijao",
    "SALADA": "Salada",
    "FRUTA": "Fruta",
    "SOBREMESA": "Sobremesa",
    "BEBIDA": "Bebida",
    "LANCHE": "Lanche",
    "ACOMPANHAMENTO": "Acompanhamento",
}

# Categorias que puxam o prato para o lado mais saudavel.
FRESH_CATEGORIES = {"SALADA", "FRUTA"}

LIGHT_KCAL = 80
HEAVY_KCAL = 200
GOOD_PROTEIN_G = 15


def friendly_date(iso: str) -> str:
    """2025-09-01 -> "segunda-feira, 1 de setembro"."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{DIAS[d.weekday()]}, {d.day} de {MESES[d.month - 1]}"


def category_label(code: str) -> str:
    return CATEGORY_LABEL.get(code.upper(), code.title())


def order_categories(codes) -> list[str]:
    """Ordena as categorias como a fila do refeitorio, sobras no fim."""
    conhecidas = [c for c in SERVING_ORDER if c in codes]
    resto = sorted(c for c in codes if c not in SERVING_ORDER)
    return conhecidas + resto


# Abreviacoes da operacao que nao fazem sentido para quem le o cardapio.
PREFIXOS_OPERACAO = ("sal.", "sal ", "sob.", "gua.")


def clean_dish_name(name: str) -> str:
    """"SAL. MIX DE ALFACE" -> "Mix de alface".

    O cardapio da cozinha usa abreviacao de categoria no nome. Isso ajuda quem
    monta a planilha e atrapalha quem le no celular.
    """
    limpo = (name or "").strip()
    minusculo = limpo.lower()
    for prefixo in PREFIXOS_OPERACAO:
        if minusculo.startswith(prefixo):
            limpo = limpo[len(prefixo):].strip(" .")
            break
    return limpo[:1].upper() + limpo[1:].lower() if limpo else limpo


def dish_weight(kcal: float | None) -> str:
    """Uma palavra sobre o quanto o prato pesa no dia."""
    if kcal is None:
        return "sem informacao"
    if kcal < LIGHT_KCAL:
        return "leve"
    if kcal <= HEAVY_KCAL:
        return "moderado"
    return "reforcado"


def dish_hint(kcal: float | None, ptn_g: float | None) -> str:
    """Frase curta para aparecer junto do prato no cardapio."""
    partes = [dish_weight(kcal)]
    if ptn_g is not None and ptn_g >= GOOD_PROTEIN_G:
        partes.append("boa fonte de proteina")
    return " · ".join(partes)


def plate_reading(
    kcal: float,
    ptn_g: float,
    target_kcal: int,
    target_ptn: int,
    has_fresh: bool,
    unknown_items: int = 0,
    known_items: int = 1,
) -> list[str]:
    """Leitura do prato montado, em frases.

    Nunca repreende nem manda comer menos: descreve onde o prato esta em relacao
    ao que a propria pessoa escolheu como objetivo, e sugere o que ainda da para
    somar. Quem prescreve e o nutricionista.

    `unknown_items` sao os itens do prato sem valor nutricional. Enquanto houver
    um, o prato nao e classificado: somar so o que tem macro da um numero menor
    que o real, e chamar isso de "prato leve" seria o app afirmar o que nao sabe.
    Prato sem nenhum item conhecido nao recebe leitura nenhuma — a mesma regra
    que vale para alergenico vale aqui: ausencia de informacao nunca vira
    afirmacao.
    """
    linhas: list[str] = []

    if unknown_items and not known_items:
        linhas.append(
            "Ainda nao consigo ler este prato: os itens de hoje estao sem informacao nutricional."
        )
    elif unknown_items:
        linhas.append(
            f"Leitura incompleta: {unknown_items} item(ns) do prato estao sem informacao nutricional."
        )
        linhas.append(
            f"Do que da para somar, o prato tem no minimo {kcal:.0f} kcal e {ptn_g:.0f} g de proteina."
        )
    else:
        proporcao = kcal / target_kcal if target_kcal else 0
        if proporcao < 0.7:
            linhas.append("Prato leve para o seu objetivo de hoje.")
        elif proporcao <= 1.1:
            linhas.append("Prato alinhado com o seu objetivo de hoje.")
        else:
            linhas.append("Prato acima do que voce marcou como alvo para hoje.")

        if ptn_g >= target_ptn:
            linhas.append(f"Proteina do dia atingida: {ptn_g:.0f} g.")
        else:
            falta = target_ptn - ptn_g
            linhas.append(f"Faltam {falta:.0f} g de proteina para o seu alvo.")

    if not has_fresh:
        linhas.append("Sem salada nem fruta no prato — vale somar uma.")

    return linhas


def week_summary(dias_registrados: int, dias_uteis: int = 5) -> str:
    """Progresso pessoal da semana, sem comparar com ninguem."""
    if dias_registrados == 0:
        return "Voce ainda nao registrou nenhuma refeicao esta semana."
    if dias_registrados >= dias_uteis:
        return f"Voce registrou todos os {dias_uteis} dias desta semana."
    return f"Voce registrou {dias_registrados} de {dias_uteis} dias desta semana."
