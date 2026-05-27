import os
from dotenv import load_dotenv

load_dotenv()

# Notion
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID") or os.getenv("DATA_SOURCE_ID")

# 다중 소스: 쉼표로 구분된 페이지 ID 목록 및 데이터베이스 ID 목록
NOTION_SOURCE_PAGES = [p.strip() for p in os.getenv("NOTION_SOURCE_PAGES", "").split(",") if p.strip()]
NOTION_SOURCE_DATABASES = [d.strip() for d in os.getenv("NOTION_SOURCE_DATABASES", "").split(",") if d.strip()]

# 계획서 페이지 (보고서 "가. 최초 계획" 섹션에 사용)
PLAN_PAGE_ID = NOTION_SOURCE_PAGES[0] if NOTION_SOURCE_PAGES else None

# OpenAI / OpenRouter
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
VISION_MODEL = os.getenv("VISION_MODEL", "openai/gpt-4o-mini")
VISION_MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "300"))

# Files
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_data_dir = os.getenv("DATA_DIR")
DATA_DIR = _data_dir if (_data_dir and not _data_dir.startswith("/app")) else os.path.join(BASE_DIR, "data")
_image_dir = os.getenv("IMAGE_DIR")
IMAGE_DIR = _image_dir if (_image_dir and not _image_dir.startswith("/app")) else os.path.join(DATA_DIR, "notion_images")

# Qdrant
_qdrant_path = os.getenv("QDRANT_PATH") or os.getenv("QDRANT_DATA_DIR")
QDRANT_PATH = _qdrant_path if (_qdrant_path and not _qdrant_path.startswith("/app")) else "./qdrant_data"
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "notion_docs")

# Chunking
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "50"))

# Langfuse
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
