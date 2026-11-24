import os

import uvicorn

from app.config import get_settings

os.environ.setdefault("OTEL_SDK_DISABLED", "true")


class QuietServer(uvicorn.Server):
    def handle_exit(self, sig: int, frame: object) -> None:
        self.should_exit = True


def main() -> None:
    settings = get_settings()
    config = uvicorn.Config(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0" if settings.is_production else "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=settings.is_production,
        log_level="info",
        timeout_graceful_shutdown=10,
    )
    QuietServer(config).run()


if __name__ == "__main__":
    main()
