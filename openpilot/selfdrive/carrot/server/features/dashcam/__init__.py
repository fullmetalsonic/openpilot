from .routes import register as register_dashcam
from .pc_transfer import register as register_pc_transfer


def register(app) -> None:
  # Replay is client-computed. The device exposes only the read-only source
  # routes registered by routes.py; legacy scan/query/cache handlers stay off.
  register_dashcam(app)
  register_pc_transfer(app)

__all__ = ["register"]
