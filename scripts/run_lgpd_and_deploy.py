from pathlib import Path
import runpy


script = Path(__file__).with_name("add_lgpd_and_deploy.py")
text = script.read_text(encoding="utf-8")
bad = 'replace_between(text, "def main", "if __name__ == \\"__main__\\"", main_block)'
good = 'replace_between(text, "def main() -> None:", "if __name__ == \\"__main__\\"", main_block)'
if bad in text:
    script.write_text(text.replace(bad, good, 1), encoding="utf-8")

runpy.run_path(str(script), run_name="__main__")
