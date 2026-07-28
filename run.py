import threading
import webbrowser

import uvicorn

from app.config import HOST, PORT


def open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}/")


def main() -> None:
    threading.Timer(1.0, open_browser).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
