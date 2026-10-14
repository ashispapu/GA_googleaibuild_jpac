"""PlantGuard backend package. Loads `.env` first, so every module (settings, auth, agents) sees its values.
Skipped under pytest so a developer's real .env (e.g. a live GEMINI_API_KEY) never changes test results."""
import sys

if "pytest" not in sys.modules:
    from app.core.dotenv import load_dotenv

    load_dotenv()
