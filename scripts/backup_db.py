import os
import shutil
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

db_path = Path(os.getenv("APETIT_DB_PATH", "apetit.db"))
backup_dir = Path("backups")

if not db_path.exists():
    raise SystemExit(f"Banco nao encontrado: {db_path}")

backup_dir.mkdir(exist_ok=True)
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
target = backup_dir / f"{db_path.stem}-{stamp}{db_path.suffix or '.db'}"

shutil.copy2(db_path, target)
print(f"Backup criado em: {target}")
