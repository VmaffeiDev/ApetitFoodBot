import sqlite3
import tempfile
import unittest
from pathlib import Path

from apetit.allergens import Declaration, Restriction, Verdict
from apetit.catalog import (
    check_menu_for_employee,
    import_menu_csv,
    init_schema,
    item_allergens,
    menu_for_date,
    pending_issues,
)
from apetit.csv_import import parse_menu_csv, parse_number, split_category
from apetit.model import MenuItem, Nutrition
from apetit.validation import validate_item

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class NumberParsingTest(unittest.TestCase):
    def test_accepts_brazilian_and_plain_decimals(self):
        self.assertEqual(parse_number("8,6"), 8.6)
        self.assertEqual(parse_number("1.234,56"), 1234.56)
        self.assertEqual(parse_number("1234.56"), 1234.56)
        self.assertEqual(parse_number("32"), 32.0)

    def test_blank_and_garbage_become_none(self):
        self.assertIsNone(parse_number(""))
        self.assertIsNone(parse_number("   "))
        self.assertIsNone(parse_number("-"))
        self.assertIsNone(parse_number("n/a"))

    def test_category_slot_is_split_from_header(self):
        self.assertEqual(split_category("PRATO PRINCIPAL"), ("PRATO PRINCIPAL", 1))
        self.assertEqual(split_category("PRATO PRINCIPAL 2"), ("PRATO PRINCIPAL", 2))
        self.assertEqual(split_category("Salada 3"), ("SALADA", 3))


class WideCsvTest(unittest.TestCase):
    def setUp(self):
        self.entries, self.issues = parse_menu_csv(load("cardapio_largo.csv"), unit="SM", meal="almoco")

    def test_reads_every_filled_slot_and_skips_empty_ones(self):
        self.assertEqual(len(self.entries), 20)
        dias = {entry.service_date for entry in self.entries}
        self.assertEqual(dias, {"2025-09-01", "2025-09-02", "2025-09-03"})

    def test_second_slot_becomes_same_category(self):
        principais = [e for e in self.entries if e.category == "PRATO PRINCIPAL" and e.service_date == "2025-09-01"]
        self.assertEqual(sorted(e.slot for e in principais), [1, 2])

    def test_cost_column_is_reported_and_never_becomes_an_item(self):
        descartes = [i for i in self.issues if i.code == "coluna_descartada"]
        self.assertEqual(len(descartes), 1)
        self.assertIn("CUSTO", descartes[0].detail)
        self.assertNotIn("CUSTO", {e.category for e in self.entries})


class LongCsvTest(unittest.TestCase):
    def test_reads_code_and_portion(self):
        entries, issues = parse_menu_csv(load("cardapio_longo.csv"), unit="KY")

        self.assertEqual(len(entries), 4)
        self.assertEqual([i for i in issues if i.blocking], [])
        contra_file = next(e for e in entries if e.item.name.startswith("CONTRA FILE"))
        self.assertEqual(contra_file.item.code, "01.01.04.280-0.9")
        self.assertEqual(contra_file.item.portion_g, 100)
        self.assertEqual(contra_file.meal, "almoco")


class AllergenCsvTest(unittest.TestCase):
    """Quando a ficha tecnica passar a exportar alergenico, o alerta liga sozinho."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_allergen_columns_are_imported(self):
        import_menu_csv(self.conn, load("cardapio_alergenicos.csv"), unit="SM")

        declarado = item_allergens(self.conn, "01.01.01.010")
        self.assertEqual(declarado["leite"], Declaration.CONTEM)
        self.assertEqual(declarado["gluten"], Declaration.NAO_CONTEM)

    def test_traces_become_may_contain(self):
        import_menu_csv(self.conn, load("cardapio_alergenicos.csv"), unit="SM")

        declarado = item_allergens(self.conn, "03.01.01.010")
        self.assertEqual(declarado["amendoim"], Declaration.PODE_CONTER)

    def test_blank_cell_stays_undeclared_instead_of_becoming_free(self):
        import_menu_csv(self.conn, load("cardapio_alergenicos.csv"), unit="SM")

        declarado = item_allergens(self.conn, "01.03.01.032")
        self.assertEqual(declarado, {})

    def test_imported_declaration_drives_the_safety_check(self):
        import_menu_csv(self.conn, load("cardapio_alergenicos.csv"), unit="SM")

        cardapio = check_menu_for_employee(self.conn, "2025-09-01", [Restriction("leite")], unit="SM")
        strogonoff = next(i for i in cardapio if i["item_code"] == "01.01.01.010")
        alface = next(i for i in cardapio if i["item_code"] == "04.01.01.005")

        self.assertIs(strogonoff["check"].verdict, Verdict.BLOQUEIO)
        self.assertIs(alface["check"].verdict, Verdict.LIBERADO)


class ValidationTest(unittest.TestCase):
    def item(self, **kwargs) -> MenuItem:
        nutrition = Nutrition(**{k: kwargs.pop(k) for k in list(kwargs) if k in Nutrition.__annotations__})
        return MenuItem(code="x", name=kwargs.pop("name", "ITEM"), nutrition=nutrition, **kwargs)

    def test_energy_inconsistent_with_macros_is_blocked(self):
        # Padrao real: bife suino declarando 37 g de gordura e 37 g de proteina.
        issues = validate_item(self.item(name="BIFE SUINO", kcal=215, cho_g=6.44, lip_g=37, ptn_g=37))

        self.assertTrue(any(i.code == "energia_inconsistente" and i.blocking for i in issues))

    def test_small_rounding_on_tiny_items_is_not_blocked(self):
        # 29 vs 41 kcal e 41% de erro relativo, mas so 12 kcal de diferenca:
        # e arredondamento de porcao pequena, nao dado corrompido.
        issues = validate_item(self.item(name="FEIJAO PRETO", kcal=29, cho_g=6, lip_g=1, ptn_g=2))

        self.assertEqual([i for i in issues if i.blocking], [])

    def test_macros_heavier_than_portion_is_blocked(self):
        item = MenuItem(
            code="x",
            name="FAROFA",
            portion_g=30,
            nutrition=Nutrition(kcal=195, cho_g=31, lip_g=7, ptn_g=3),
        )

        issues = validate_item(item)

        self.assertTrue(any(i.code == "macro_maior_que_porcao" and i.blocking for i in issues))

    def test_missing_macros_warns_but_does_not_block(self):
        issues = validate_item(self.item(name="PICADO MISTO"))

        self.assertTrue(any(i.code == "macro_incompleto" for i in issues))
        self.assertEqual([i for i in issues if i.blocking], [])

    def test_negative_value_is_blocked(self):
        issues = validate_item(self.item(name="ERRO", kcal=100, cho_g=-5, lip_g=2, ptn_g=3))

        self.assertTrue(any(i.code == "valor_negativo" and i.blocking for i in issues))


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_blocked_item_never_reaches_the_menu(self):
        result = import_menu_csv(self.conn, load("cardapio_largo.csv"), unit="SM", batch="lote-1")

        publicados = {e.item.name for e in result.published}
        bloqueados = {e.item.name for e in result.blocked}
        self.assertIn("BIFE SUINO AO MOLHO BARBECUE", bloqueados)
        self.assertIn("SOPA CREME DE ABOBORA", bloqueados)
        self.assertNotIn("BIFE SUINO AO MOLHO BARBECUE", publicados)

        cardapio = menu_for_date(self.conn, "2025-09-01", unit="SM")
        self.assertNotIn("BIFE SUINO AO MOLHO BARBECUE", {row["name"] for row in cardapio})
        self.assertIn("CARNE ASSADA AO MOLHO", {row["name"] for row in cardapio})

    def test_blocked_items_land_in_the_review_queue_with_a_reason(self):
        import_menu_csv(self.conn, load("cardapio_largo.csv"), unit="SM", batch="lote-1")

        fila = pending_issues(self.conn, batch="lote-1")
        motivos = {row["item_name"]: row["detail"] for row in fila}
        self.assertIn("BIFE SUINO AO MOLHO BARBECUE", motivos)
        self.assertIn("kcal", motivos["BIFE SUINO AO MOLHO BARBECUE"])

    def test_item_without_macros_is_published_flagged(self):
        result = import_menu_csv(self.conn, load("cardapio_largo.csv"), unit="SM")

        self.assertIn("PICADO MISTO", {e.item.name for e in result.published})
        row = next(r for r in menu_for_date(self.conn, "2025-09-03", unit="SM") if r["name"] == "PICADO MISTO")
        self.assertIsNone(row["kcal"])

    def test_reimport_updates_instead_of_duplicating(self):
        first = import_menu_csv(self.conn, load("cardapio_largo.csv"), unit="SM")
        import_menu_csv(self.conn, load("cardapio_largo.csv"), unit="SM")

        total = self.conn.execute("SELECT COUNT(*) AS n FROM menu_entry").fetchone()["n"]
        self.assertEqual(total, len(first.published))

    def test_review_queue_groups_by_dish_not_by_occurrence(self):
        # A mesma ficha errada reaparece em varios dias do mes. Quem revisa
        # corrige uma vez, entao o resumo precisa contar fichas, nao linhas.
        result = import_menu_csv(self.conn, load("cardapio_largo.csv"), unit="SM")

        bloqueados = [(i, n) for i, n, _ in result.grouped_issues() if i.blocking]
        nomes = {issue.item_name for issue, _ in bloqueados}
        self.assertEqual(nomes, {"BIFE SUINO AO MOLHO BARBECUE", "SOPA CREME DE ABOBORA"})
        self.assertIn("ficha(s) tecnica(s)", result.summary())

    def test_portion_is_kept_when_the_export_has_it(self):
        import_menu_csv(self.conn, load("cardapio_longo.csv"), unit="KY")

        row = self.conn.execute("SELECT portion_g FROM menu_item WHERE code = '01.01.04.280-0.9'").fetchone()
        self.assertEqual(row["portion_g"], 100)


if __name__ == "__main__":
    unittest.main()
