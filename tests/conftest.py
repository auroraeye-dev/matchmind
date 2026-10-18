import os

# Tests never call a real model.
os.environ["LLM_PROVIDER"] = "offline"
