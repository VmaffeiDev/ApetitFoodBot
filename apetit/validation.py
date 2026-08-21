"""Validacao nutricional na entrada da importacao.

Num app que orienta funcionario, macro errado vira orientacao errada. Por isso
o item que nao passa nao e publicado: vai para a fila de revisao do
nutricionista com o motivo.

As regras saem da analise dos cardapios reais da Apetit, onde a maioria dos
itens fecha e alguns poucos tem macro corrompido (ex.: um bife suino de 100 g
declarando 37 g de gordura e 37 g de proteina ao mesmo tempo).
"""

from .model import Issue, MenuItem

# Tolerancia do teste de Atwater. Precisa das duas: a relativa sozinha acusa
# saladas de 4 kcal, onde 2 kcal de arredondamento viram 50% de erro.
ATWATER_REL_TOL = 0.25
ATWATER_ABS_TOL_KCAL = 30.0

# Acima disso, uma porcao individual de refeitorio e implausivel.
MAX_PLAUSIBLE_KCAL = 900.0


def validate_item(item: MenuItem, **context: str) -> list[Issue]:
    """Retorna os problemas do item. Lista vazia significa pronto para publicar."""
    issues: list[Issue] = []

    def add(severity: str, code: str, detail: str) -> None:
        issues.append(
            Issue(
                severity=severity,
                code=code,
                detail=detail,
                item_name=item.name,
                unit=context.get("unit", ""),
                service_date=context.get("service_date", ""),
                category=context.get("category", ""),
            )
        )

    if not item.name.strip():
        add("block", "nome_ausente", "Item sem nome.")
        return issues

    nutrition = item.nutrition
    values = {
        "kcal": nutrition.kcal,
        "CHO": nutrition.cho_g,
        "LIP": nutrition.lip_g,
        "PTN": nutrition.ptn_g,
    }

    negativos = [label for label, value in values.items() if value is not None and value < 0]
    if negativos:
        add("block", "valor_negativo", f"Valor negativo em: {', '.join(negativos)}.")

    if not nutrition.complete:
        faltando = [label for label, value in values.items() if value is None]
        add("warn", "macro_incompleto", f"Sem informacao nutricional completa: falta {', '.join(faltando)}.")
        return issues

    calculado = nutrition.atwater_kcal()
    diferenca = abs(calculado - nutrition.kcal)
    relativo = diferenca / max(nutrition.kcal, 1.0)
    if relativo > ATWATER_REL_TOL and diferenca > ATWATER_ABS_TOL_KCAL:
        add(
            "block",
            "energia_inconsistente",
            f"Energia declarada ({nutrition.kcal:.0f} kcal) nao bate com os macros "
            f"({calculado:.0f} kcal por 4/9/4). Diferenca de {diferenca:.0f} kcal.",
        )

    if item.portion_g is not None and nutrition.macros_g is not None:
        if nutrition.macros_g > item.portion_g:
            add(
                "block",
                "macro_maior_que_porcao",
                f"Macros somam {nutrition.macros_g:.0f} g numa porcao de {item.portion_g:.0f} g.",
            )

    if nutrition.kcal > MAX_PLAUSIBLE_KCAL:
        add("warn", "energia_alta", f"Porcao com {nutrition.kcal:.0f} kcal. Confirmar se a porcao esta correta.")

    return issues
