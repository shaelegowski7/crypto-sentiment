from transformers import pipeline
import torch

# Use GPU if available, otherwise CPU
device = 0 if torch.cuda.is_available() else -1

# Load FinBERT - a sentiment model trained specifically on financial text
sentiment_pipeline = pipeline(
    "text-classification",
    model="ProsusAI/finbert",
    device=device
)

LABEL_MAP = {
    "positive": 1.0,
    "negative": -1.0,
    "neutral": 0.0
}

def analyse_sentiment(text: str) -> dict:
    if not text:
        return {"label": "neutral", "score": 0.0}

    # FinBERT has a 512 token limit so truncate long headlines
    result = sentiment_pipeline(text, truncation=True, max_length=512)[0]

    label = result["label"].lower()
    confidence = result["score"]

    # Convert to a -1 to +1 scale using confidence as weight
    score = LABEL_MAP.get(label, 0.0) * confidence

    return {
        "label": label,
        "score": round(score, 4)
    }


def analyse_batch(headlines: list) -> list:
    return [analyse_sentiment(h["title"]) for h in headlines]