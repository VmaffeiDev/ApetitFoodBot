import sqlite3
import tempfile
import unittest
from pathlib import Path

from apetit.allergen_sheet import build_template, coverage_summary, parse_sheet
from apetit.allergens import ALLERGENS, Declaration, Restriction, Verdict
from apetit.catalog import (
    allergen_coverage,
    apply_allergen_sheet,
    check_menu_for_employee,
    import_menu_csv,
    init_schema,
    item_allergens,
    items_with_allergens,
    set_item_allergens,
)

FIXTURES = Path(__file__).parent / "fixtures"


class SheetBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        import_menu_csv(
            self.conn,
            (FIXTURES / "cardapio_largo.csv").read_text(encoding="utf-8"),
            unit="SM",
        )

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)


class ExportacaoTest(SheetBase):
    def test_template_has_one_column_per_allergen(self):
        planilha = build_template(items_with_allergens(self.conn))

        cabecalho = planilha.splitlines()[0].split(";")
        self.assertEqual(cabecalho[:2], ["codigo", "prato"])
        self.assertEqual(set(cabecalho[2:]), set(ALLERGENS))

    def test_most_frequent_dishes_come_first(self):
        # Declarar o arroz que sai todo dia rende mais que o prato que saiu uma vez.
        itens = items_with_allergens(self.conn)

        primeiros = [nome for _, nome, _ in itens[:3]]
        self.assertIn("ARROZ PARBOILIZADO", primeiros)

    def test_what_is_already_declared_comes_prefilled(self):
        set_item_allergens(self.conn, "arroz_parboilizado", {"gluten": "nao_contem", "leite": "contem"})

        planilha = build_template(items_with_allergens(self.conn))

        linha = next(l for l in planilha.splitlines() if l.startswith("arroz_parboilizado;"))
        colunas = dict(zip(planilha.splitlines()[0].split(";"), linha.split(";")))
        self.assertEqual(colunas["gluten"], "nao")
        self.assertEqual(colunas["leite"], "sim")


class LeituraTest(unittest.TestCase):
    def test_reads_the_words_a_person_actually_writes(self):
        planilha = (
            "codigo;prato;leite;gluten;ovos\n"
            "prato_a;PRATO A;sim;nao;pode conter\n"
            "prato_b;PRATO B;S;N;tracos\n"
        )

        declaracoes, avisos = parse_sheet(planilha)

        self.assertEqual(avisos, [])
        self.assertEqual(declaracoes["prato_a"]["leite"], "contem")
        self.assertEqual(declaracoes["prato_a"]["gluten"], "nao_contem")
        self.assertEqual(declaracoes["prato_a"]["ovos"], "pode_conter")
        self.assertEqual(declaracoes["prato_b"]["leite"], "contem")
        self.assertEqual(declaracoes["prato_b"]["ovos"], "pode_conter")

    def test_blank_cell_stays_undeclared_instead_of_becoming_free(self):
        planilha = "codigo;prato;leite;gluten\nprato_a;PRATO A;sim;\n"

        declaracoes, _ = parse_sheet(planilha)

        self.assertEqual(declaracoes["prato_a"], {"leite": "contem"})
        self.assertNotIn("gluten", declaracoes["prato_a"])

    def test_unreadable_cell_warns_and_is_not_guessed(self):
        planilha = "codigo;prato;leite\nprato_a;PRATO A;talvez\n"

        declaracoes, avisos = parse_sheet(planilha)

        self.assertEqual(declaracoes, {})
        self.assertEqual(len(avisos), 1)
        self.assertIn("talvez", avisos[0])

    def test_wrong_file_is_rejected_with_a_reason(self):
        _, avisos = parse_sheet("nome;preco\nx;1\n")

        self.assertIn("coluna 'codigo'", avisos[0])


class AplicacaoTest(SheetBase):
    def test_sheet_round_trip_drives_the_safety_check(self):
        planilha = build_template(items_with_allergens(self.conn))
        cabecalho = planilha.splitlines()[0].split(";")
        indice_leite = cabecalho.index("leite")

        # O nutricionista preenche: strogonoff leva leite, alface nao leva nada.
        linhas = [planilha.splitlines()[0]]
        for linha in planilha.splitlines()[1:]:
            campos = linha.split(";")
            if campos[0] == "carne_assada_ao_molho":
                campos[indice_leite] = "sim"
            elif campos[0] == "sal_mix_de_alface":
                campos[indice_leite] = "nao"
            linhas.append(";".join(campos))

        declaracoes, avisos = parse_sheet("\n".join(linhas))
        aplicadas, erros = apply_allergen_sheet(self.conn, declaracoes)

        self.assertEqual(avisos, [])
        self.assertEqual(erros, [])
        self.assertEqual(aplicadas, 2)

        cardapio = check_menu_for_employee(self.conn, "2025-09-01", [Restriction("leite")], unit="SM")
        carne = next(i for i in cardapio if i["item_code"] == "carne_assada_ao_molho")
        alface = next(i for i in cardapio if i["item_code"] == "sal_mix_de_alface")
        self.assertIs(carne["check"].verdict, Verdict.BLOQUEIO)
        self.assertIs(alface["check"].verdict, Verdict.LIBERADO)

    def test_unknown_dish_is_reported_not_silently_created(self):
        aplicadas, erros = apply_allergen_sheet(self.conn, {"prato_que_nao_existe": {"leite": "contem"}})

        self.assertEqual(aplicadas, 0)
        self.assertIn("prato_que_nao_existe", erros[0])

    def test_invalid_allergen_is_reported(self):
        _, erros = apply_allergen_sheet(self.conn, {"arroz_parboilizado": {"kriptonita": "contem"}})

        self.assertEqual(len(erros), 1)
        self.assertEqual(item_allergens(self.conn, "arroz_parboilizado"), {})


class CoberturaTest(SheetBase):
    def test_starts_at_zero_and_says_so(self):
        cobertura = allergen_coverage(self.conn)

        self.assertEqual(cobertura["completos"], 0)
        self.assertGreater(cobertura["total"], 0)
        self.assertIn("0 de", coverage_summary(cobertura["total"], 0, 0))

    def test_partial_declaration_counts_as_partial_not_complete(self):
        set_item_allergens(self.conn, "arroz_parboilizado", {"leite": "nao_contem"})

        cobertura = allergen_coverage(self.conn)

        self.assertEqual(cobertura["completos"], 0)
        self.assertEqual(cobertura["parciais"], 1)

    def test_full_declaration_counts_as_complete(self):
        set_item_allergens(self.conn, "arroz_parboilizado", {c: "nao_contem" for c in ALLERGENS})

        cobertura = allergen_coverage(self.conn)

        self.assertEqual(cobertura["completos"], 1)
        codigos_faltando = {code for code, _, _ in cobertura["faltando"]}
        self.assertNotIn("arroz_parboilizado", codigos_faltando)

    def test_summary_explains_what_incomplete_means_for_the_employee(self):
        texto = coverage_summary(10, 3, 2)

        self.assertIn("3 de 10", texto)
        self.assertIn("nao consegue confirmar", texto)


if __name__ == "__main__":
    unittest.main()
