import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from menubar import menu_title  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_menu_title():
    assert menu_title(0) == "🧾"
    assert menu_title(7) == "🧾 7"


def test_make_app_builds_a_valid_bundle(tmp_path):
    shutil.copy(ROOT / "make_app.command", tmp_path / "make_app.command")
    subprocess.run(["zsh", str(tmp_path / "make_app.command")], check=True, capture_output=True)
    app = tmp_path / "Agent Receipt.app"
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleExecutable"] == "AgentReceipt" and plist["LSUIElement"] is True
    launcher = app / "Contents" / "MacOS" / "AgentReceipt"
    assert launcher.stat().st_mode & 0o111
    assert "src/menubar.py" in launcher.read_text()
    assert subprocess.run(["zsh", "-n", str(launcher)]).returncode == 0
