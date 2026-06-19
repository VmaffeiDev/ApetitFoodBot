import unittest

import nutrition


class NutritionLogicTest(unittest.TestCase):
    def test_calculates_mifflin_targets_for_muscle_gain(self):
        targets = nutrition.calculate_targets(
            weight_kg=70,
            height_cm=175,
            age=30,
            sex="male",
            activity="moderate",
            goal="Ganhar massa",
        )

        self.assertEqual(targets.resting_calories, 1649)
        self.assertEqual(targets.maintenance_calories, 2556)
        self.assertEqual(targets.target_calories, 2811)
        self.assertEqual(targets.protein_grams, 126)
        self.assertIn("180-220 g", targets.portion_guidance)

    def test_rejects_profile_outside_supported_adult_range(self):
        with self.assertRaisesRegex(ValueError, "19 a 78"):
            nutrition.calculate_targets(60, 165, 17, "female", "light", "Manter equilibrio")

    def test_nutrition_signal_respects_goal_and_restriction(self):
        self.assertEqual(nutrition.nutrition_signal(3), "green")
        self.assertEqual(nutrition.nutrition_signal(0), "yellow")
        self.assertEqual(nutrition.nutrition_signal(5, has_restriction_conflict=True), "red")

    def test_streak_increments_only_on_consecutive_days(self):
        self.assertEqual(nutrition.next_streak(None, "2026-06-19", 0), 1)
        self.assertEqual(nutrition.next_streak("2026-06-18", "2026-06-19", 4), 5)
        self.assertEqual(nutrition.next_streak("2026-06-19", "2026-06-19", 5), 5)
        self.assertEqual(nutrition.next_streak("2026-06-10", "2026-06-19", 5), 1)

    def test_badges_follow_documented_thresholds(self):
        self.assertEqual(nutrition.earned_badges(0, 0, 0), set())
        self.assertIn("first_step", nutrition.earned_badges(5, 1, 0))
        self.assertIn("on_fire", nutrition.earned_badges(50, 3, 0))
        self.assertIn("green_guardian", nutrition.earned_badges(50, 2, 3))
        self.assertIn("healthy_life", nutrition.earned_badges(100, 2, 0))
        self.assertIn("apetit_master", nutrition.earned_badges(500, 7, 3))


if __name__ == "__main__":
    unittest.main()
