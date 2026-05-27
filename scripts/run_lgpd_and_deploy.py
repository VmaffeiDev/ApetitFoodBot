from pathlib import Path
import runpy


script = Path(__file__).with_name("add_lgpd_and_deploy.py")
text = script.read_text(encoding="utf-8")

replacements = {
    'replace_between(text, "def main", "if __name__ == \\"__main__\\"", main_block)':
        'replace_between(text, "def main() -> None:", "if __name__ == \\"__main__\\"", main_block)',
    "save_client_block = '''": "save_client_block = r'''",
    "data_block = '''": "data_block = r'''",
    "hydrate_block = '''": "hydrate_block = r'''",
    "helpers_block = '''": "helpers_block = r'''",
    "consent_buttons = '''": "consent_buttons = r'''",
    "profile_payload_block = '''": "profile_payload_block = r'''",
    "consent_flow_block = '''": "consent_flow_block = r'''",
    "start_block = '''": "start_block = r'''",
    "registration_callback_block = '''": "registration_callback_block = r'''",
    "message_block = '''": "message_block = r'''",
    "main_block = '''": "main_block = r'''",
}

for old, new in replacements.items():
    text = text.replace(old, new, 1)

script.write_text(text, encoding="utf-8")
runpy.run_path(str(script), run_name="__main__")
