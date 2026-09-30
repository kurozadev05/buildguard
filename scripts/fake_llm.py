"""Stand-alone simulated LLM provider (Anthropic + OpenAI wire formats) for local/integration testing. Never used in production.
python scripts/fake_llm.py [port]   -> answers slowly, word by word, so streaming is visible."""
import sys
import time

sys.path.insert(0, ".")
from tests.fake_provider import Server, fake  # noqa: E402

fake.default = {"text": "Take one cube sample for every five cubic metres of concrete placed, and never fewer than one per shift [K1].", "delay": 0.4, "chunk_delay": 0.15}
s = Server()
s.server.config.port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
print("fake llm at", s.start(), flush=True)
while True:
    time.sleep(3600)
