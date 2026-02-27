import re
import shutil
import uuid
from pathlib import Path

GNS3_ROOT = Path("/data/gns3")
SNAPSHOT_ROOT = Path("/data/snapshots")


def extract_hostname(cfg_text: str) -> str:
    match = re.search(r"^hostname\s+(\S+)", cfg_text, re.MULTILINE)
    return match.group(1) if match else f"device_{uuid.uuid4().hex[:6]}"


def ingest_gns3_configs(snapshot_name: str):
    snapshot_path = SNAPSHOT_ROOT / snapshot_name / "configs"
    snapshot_path.mkdir(parents=True, exist_ok=True)

    for cfg in GNS3_ROOT.rglob("*startup-config.cfg"):
        text = cfg.read_text(errors="ignore")
        hostname = extract_hostname(text)
        dst = snapshot_path / f"{hostname}.cfg"
        shutil.copy(cfg, dst)

    return snapshot_path


if __name__ == "__main__":
    snap = ingest_gns3_configs("gns3_lab_snapshot_v1")
    print(f"[BATFISH SNAPSHOT CREATED] -> {snap}")
