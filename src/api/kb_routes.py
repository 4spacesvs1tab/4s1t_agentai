# Backward-compatibility shim — B2 refactor (sprint4).
# The monolithic kb_routes.py has been decomposed into src/api/kb/ package.
# Any external import of `from api.kb_routes import router` continues to work.
from api.kb import router as kb_router  # noqa: F401
from api.kb import router  # noqa: F401
