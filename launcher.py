"""Entry point for the Windows app (the file PyInstaller builds DevilsWorkshop.exe from).

Only the main process starts the app. The app window runs in a second process;
in the packaged .exe, freeze_support() hands that process over to the window code.
"""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from devils_workshop.ui.main import main

    raise SystemExit(main())
