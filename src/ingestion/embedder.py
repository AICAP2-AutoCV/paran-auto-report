"""OpenAI 임베딩"""

import time
from typing import List

from openai import OpenAI
from langchain_core.embeddings import Embeddings

from ..config import EMBED_BATCH_SIZE


class OpenAIEmbedder(Embeddings):
    def __init__(self, api_key: str, model: str, base_url: str, batch_size: int = EMBED_BATCH_SIZE):
        if not api_key:
            raise ValueError("OPENAI_API_KEY 또는 OPENROUTER_API_KEY가 필요합니다.")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.batch_size = batch_size

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        all_embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t.strip() or " " for t in texts[i: i + self.batch_size]]
            for attempt in range(3):
                try:
                    resp = self.client.embeddings.create(model=self.model, input=batch)
                    all_embeddings.extend(d.embedding for d in resp.data)
                    print(f"  임베딩: {min(i + self.batch_size, len(texts))}/{len(texts)}")
                    time.sleep(0.3)
                    break
                except Exception as e:
                    wait = (attempt + 1) * 2
                    print(f"  ⚠️  재시도 {attempt + 1}/3 ({wait}s): {e}")
                    if attempt < 2:
                        time.sleep(wait)
                    else:
                        raise
        return all_embeddings

    def embed_query(self, text: str) -> List[float]:
        resp = self.client.embeddings.create(model=self.model, input=[text.strip() or " "])
        return resp.data[0].embedding
