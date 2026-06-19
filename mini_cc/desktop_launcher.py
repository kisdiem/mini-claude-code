from __future__ import annotations

import traceback
from pathlib import Path
from tkinter import messagebox


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / ".mini_cc" / "desktop-error.log"


def main() -> int:
    try:
        from .desktop_app import main as app_main

        return app_main()
    except Exception as exc:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            messagebox.showerror(
                "Mini Claude Code 启动失败",
                f"桌面软件启动失败：{exc}\n\n错误日志：\n{LOG_PATH}",
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
