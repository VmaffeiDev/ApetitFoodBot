from dataclasses import dataclass
from datetime import date


ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "high": 1.725,
}

ACTIVITY_LABELS = {
    "sedentary": "Sedentaria",
    "light": "Leve (1-3 dias/semana)",
    "moderate": "Moderada (3-5 dias/semana)",
    "high": "Alta (6-7 dias/semana)",
}

SEX_LABELS = {
    "male": "Masculino",
    "female": "Feminino",
}

GOAL_FACTORS = {
    "Perder peso": 0.85,
    "Ganhar massa": 1.10,
    "Manter equilibrio": 1.0,
    "Alimentacao mais saudavel": 1.0,
    "Praticidade na rotina": 1.0,
}

PROTEIN_FACTORS = {
    "Perder peso": 1.6,
    "Ganhar massa": 1.8,
    "Manter equilibrio": 1.4,
    "Alimentacao mais saudavel": 1.4,
    "Praticidade na rotina": 1.4,
}

PORTION_GUIDANCE = {
    "Perder peso": "Arroz: 1-2 colheres; feijao: 1 concha; proteina: 120-150 g.",
    "Manter equilibrio": "Arroz: 2-3 colheres; feijao: 1 concha media; proteina: 150 g.",
    "Ganhar massa": "Arroz: 3-5 colheres; feijao: 1-2 conchas; proteina: 180-220 g.",
    "Alimentacao mais saudavel": "Arroz: 2-3 colheres; feijao: 1 concha media; proteina: 150 g.",
    "Praticidade na rotina": "Arroz: 2-3 colheres; feijao: 1 concha media; proteina: 150 g.",
}

BADGES = {
    "first_step": "Primeiro Passo",
    "on_fire": "Em Chamas",
    "green_guardian": "Guardiao Verde",
    "healthy_life": "Vida Saudavel",
    "apetit_master": "Mestre Apetit",
}


@dataclass(frozen=True)
class NutritionTargets:
    resting_calories: int
    maintenance_calories: int
    target_calories: int
    protein_grams: int
    portion_guidance: str


def validate_profile(weight_kg: float, height_cm: float, age: int, sex: str, activity: str, goal: str) -> None:
    if not 35 <= weight_kg <= 300:
        raise ValueError("O peso deve estar entre 35 e 300 kg.")
    if not 120 <= height_cm <= 230:
        raise ValueError("A altura deve estar entre 120 e 230 cm.")
    if not 19 <= age <= 78:
        raise ValueError("A estimativa esta disponivel para a faixa de 19 a 78 anos validada no estudo.")
    if sex not in SEX_LABELS:
        raise ValueError("Selecione o sexo usado pela formula.")
    if activity not in ACTIVITY_FACTORS:
        raise ValueError("Selecione um nivel de atividade valido.")
    if goal not in GOAL_FACTORS:
        raise ValueError("Selecione um objetivo valido.")


def resting_calories(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    sex_adjustment = 5 if sex == "male" else -161
    return 10 * weight_kg + 6.25 * height_cm - 5 * age + sex_adjustment


def calculate_targets(
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: str,
    activity: str,
    goal: str,
) -> NutritionTargets:
    validate_profile(weight_kg, height_cm, age, sex, activity, goal)
    resting = resting_calories(weight_kg, height_cm, age, sex)
    maintenance = resting * ACTIVITY_FACTORS[activity]
    target = maintenance * GOAL_FACTORS[goal]
    protein = weight_kg * PROTEIN_FACTORS[goal]
    return NutritionTargets(
        resting_calories=round(resting),
        maintenance_calories=round(maintenance),
        target_calories=round(target),
        protein_grams=round(protein),
        portion_guidance=PORTION_GUIDANCE[goal],
    )


def nutrition_signal(goal_score: int, has_restriction_conflict: bool = False) -> str:
    if has_restriction_conflict:
        return "red"
    if goal_score >= 2:
        return "green"
    return "yellow"


def next_streak(last_active: str | None, current: str, streak: int) -> int:
    if not last_active:
        return 1
    last_date = date.fromisoformat(last_active)
    current_date = date.fromisoformat(current)
    difference = (current_date - last_date).days
    if difference <= 0:
        return max(streak, 1)
    if difference == 1:
        return streak + 1
    return 1


def earned_badges(points: int, streak: int, salad_events: int) -> set[str]:
    badges = set()
    if points > 0:
        badges.add("first_step")
    if streak >= 3:
        badges.add("on_fire")
    if salad_events >= 3:
        badges.add("green_guardian")
    if points >= 100:
        badges.add("healthy_life")
    if points >= 500 and streak >= 7:
        badges.add("apetit_master")
    return badges
