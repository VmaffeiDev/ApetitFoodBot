"""Um banco que ja existe continua funcionando depois da renomeacao da coluna.

`telegram_id` virou `pessoa_id`. Num banco vazio isso e so um nome; num banco
com gente cadastrada, e uma migracao — e migracao que erra aqui nao da erro de
coluna: da a alergia de uma pessoa lida contra o historico de outra, ou o
historico inteiro de alguem desaparecendo em silencio.

O teste monta um banco **com o esquema antigo**, escrito a mao para nao depender
do esquema atual, poe dado nele, roda o `init_schema` e cobra tres coisas: que
nenhuma linha mudou, que as chaves estrangeiras continuam ligando, e que o
dominio le a mesma resposta de antes — inclusive o prato bloqueado.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apetit.catalog import _migrate_pessoa_id, init_schema
from apetit.diario import cardapio_de
from apetit.profile import load_employee
from apetit.tracking import total_points

# O esquema como era antes da renomeacao, nas tabelas que importam para o teste.
# Copiado a mao de proposito: se viesse do `SCHEMA` atual, ele mudaria junto com
# o codigo e o teste pararia de testar migracao nenhuma.
ANTIGO = """
CREATE TABLE menu_item (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, portion_g REAL, kcal REAL,
    cho_g REAL, lip_g REAL, ptn_g REAL, updated_at TEXT NOT NULL
);
CREATE TABLE menu_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT, unit TEXT NOT NULL,
    service_date TEXT NOT NULL, meal TEXT NOT NULL, category TEXT NOT NULL,
    slot INTEGER NOT NULL DEFAULT 1,
    item_code TEXT NOT NULL REFERENCES menu_item(code), created_at TEXT NOT NULL,
    UNIQUE (unit, service_date, meal, category, slot)
);
CREATE TABLE menu_item_allergen (
    item_code TEXT NOT NULL REFERENCES menu_item(code),
    allergen_code TEXT NOT NULL, status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
    PRIMARY KEY (item_code, allergen_code)
);
CREATE TABLE employee (
    telegram_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
    apetit_unit TEXT NOT NULL DEFAULT '', client_company TEXT NOT NULL DEFAULT '',
    sector TEXT NOT NULL DEFAULT '', goal TEXT NOT NULL DEFAULT '',
    consent_accepted INTEGER NOT NULL DEFAULT 0, consented_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE employee_restriction (
    telegram_id INTEGER NOT NULL REFERENCES employee(telegram_id),
    allergen_code TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'alergia',
    created_at TEXT NOT NULL, PRIMARY KEY (telegram_id, allergen_code)
);
CREATE TABLE consumption (
    id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
    service_date TEXT NOT NULL, meal TEXT NOT NULL DEFAULT 'almoco',
    item_code TEXT NOT NULL, item_name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '', quantity INTEGER NOT NULL DEFAULT 1,
    kcal REAL, cho_g REAL, lip_g REAL, ptn_g REAL,
    source TEXT NOT NULL DEFAULT 'montado', logged_at TEXT NOT NULL
);
CREATE TABLE points_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
    rule_code TEXT NOT NULL, points INTEGER NOT NULL,
    reference_date TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE (telegram_id, rule_code, reference_date)
);
"""

AGORA = "2025-09-01T12:00:00+00:00"
DIA = "2025-09-01"
PESSOA = 777


class MigracaoTest(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.caminho = Path(self.pasta.name) / "antigo.db"
        conn = self.abrir()
        conn.executescript(ANTIGO)

        for codigo, nome, kcal, ptn in (
            ("ovo_cozido", "Ovo cozido", 70.0, 6.0),
            ("arroz_parboilizado", "Arroz parboilizado", 130.0, 2.5),
        ):
            conn.execute(
                "INSERT INTO menu_item (code, name, kcal, ptn_g, updated_at)"
                " VALUES (?, ?, ?, ?, ?)", (codigo, nome, kcal, ptn, AGORA))
            conn.execute(
                "INSERT INTO menu_entry (unit, service_date, meal, category, slot,"
                " item_code, created_at) VALUES ('SM', ?, 'almoco', 'GUARNICAO', ?, ?, ?)",
                (DIA, 1 if codigo == "ovo_cozido" else 2, codigo, AGORA))
        # O ovo declara conter ovos: e o que faz o veredito ser bloqueio.
        conn.execute(
            "INSERT INTO menu_item_allergen (item_code, allergen_code, status, updated_at)"
            " VALUES ('ovo_cozido', 'ovos', 'contem', ?)", (AGORA,))

        conn.execute(
            "INSERT INTO employee (telegram_id, name, apetit_unit, client_company,"
            " sector, goal, consent_accepted, created_at, updated_at)"
            " VALUES (?, 'Mariana', 'SM', 'Industria Exemplo', 'Producao',"
            " 'Manter o equilibrio', 1, ?, ?)", (PESSOA, AGORA, AGORA))
        conn.execute(
            "INSERT INTO employee_restriction (telegram_id, allergen_code, created_at)"
            " VALUES (?, 'ovos', ?)", (PESSOA, AGORA))
        conn.execute(
            "INSERT INTO consumption (telegram_id, service_date, item_code, item_name,"
            " quantity, kcal, ptn_g, logged_at) VALUES (?, ?, 'arroz_parboilizado',"
            " 'Arroz parboilizado', 2, 260.0, 5.0, ?)", (PESSOA, DIA, AGORA))
        conn.execute(
            "INSERT INTO points_event (telegram_id, rule_code, points, reference_date,"
            " created_at) VALUES (?, 'registro', 10, ?, ?)", (PESSOA, DIA, AGORA))
        conn.commit()
        conn.close()

    def abrir(self):
        conn = sqlite3.connect(self.caminho)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def retrato(self, conn) -> dict:
        saida = {}
        for tabela in ("employee", "employee_restriction", "consumption", "points_event"):
            saida[tabela] = [
                {k: linha[k] for k in linha.keys()}
                for linha in conn.execute(f"SELECT * FROM {tabela} ORDER BY rowid")
            ]
        return saida

    def colunas(self, conn, tabela) -> set:
        return {linha["name"] for linha in conn.execute(f"PRAGMA table_info({tabela})")}

    # ---------- a renomeacao em si ----------

    def test_a_coluna_e_renomeada_em_todas_as_tabelas(self):
        conn = self.abrir()
        try:
            renomeadas = _migrate_pessoa_id(conn)
            self.assertEqual(
                sorted(renomeadas),
                ["consumption", "employee", "employee_restriction", "points_event"])
            for tabela in renomeadas:
                self.assertIn("pessoa_id", self.colunas(conn, tabela))
                self.assertNotIn("telegram_id", self.colunas(conn, tabela))
        finally:
            conn.close()

    def test_rodar_de_novo_nao_faz_nada(self):
        """`init_schema` roda a cada subida do servidor: precisa ser idempotente."""
        conn = self.abrir()
        try:
            init_schema(conn)
            self.assertEqual(_migrate_pessoa_id(conn), [], "a segunda vez nao tem o que fazer")
            init_schema(conn)
            self.assertIn("pessoa_id", self.colunas(conn, "employee"))
        finally:
            conn.close()

    def test_nenhum_dado_se_perde(self):
        antes = None
        conn = self.abrir()
        try:
            antes = self.retrato(conn)
        finally:
            conn.close()

        conn = self.abrir()
        try:
            init_schema(conn)
            depois = self.retrato(conn)
        finally:
            conn.close()

        for tabela, linhas in antes.items():
            esperado = [
                {("pessoa_id" if k == "telegram_id" else k): v for k, v in linha.items()}
                for linha in linhas
            ]
            self.assertEqual(esperado, depois[tabela], f"{tabela} mudou na migracao")

    def test_a_chave_estrangeira_continua_ligando(self):
        """O `RENAME COLUMN` tem de reescrever o `REFERENCES` junto.

        Se nao reescrevesse, a restricao apontaria para uma coluna que nao existe
        mais e o banco passaria a aceitar restricao de alergia orfa — uma alergia
        registrada para ninguem.
        """
        conn = self.abrir()
        try:
            init_schema(conn)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            destinos = {
                (linha["table"], linha["to"])
                for linha in conn.execute("PRAGMA foreign_key_list(employee_restriction)")
            }
            self.assertIn(("employee", "pessoa_id"), destinos)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO employee_restriction (pessoa_id, allergen_code, created_at)"
                    " VALUES (999999, 'leite', ?)", (AGORA,))
        finally:
            conn.close()

    # ---------- e o que a pessoa veria ----------

    def test_o_dominio_le_a_mesma_coisa_depois_da_migracao(self):
        """O que importa nao e a coluna: e o prato que continua bloqueado."""
        conn = self.abrir()
        try:
            init_schema(conn)
            pessoa = load_employee(conn, PESSOA)
            self.assertEqual(pessoa.name, "Mariana")
            self.assertEqual([r.allergen for r in pessoa.restrictions], ["ovos"])

            cardapio = cardapio_de(conn, pessoa, DIA)
            bloqueados = [
                i["item_code"] for i in cardapio if i["check"].verdict.value == "bloqueio"
            ]
            self.assertEqual(bloqueados, ["ovo_cozido"],
                             "a alergia da pessoa sobreviveu a migracao")
            self.assertEqual(total_points(conn, PESSOA), 10)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
