from transformers import pipeline
import torch

device = 0 if torch.cuda.is_available() else -1

sentiment_pipeline = pipeline(
    "text-classification",
    model="ProsusAI/finbert",
    device=device,
    top_k=None  # return all three class probs
)

def analyse_sentiment(text: str) -> dict:
    if not text:
        return {"label": "neutral", "score": 0.0}

    results = sentiment_pipeline(text, truncation=True, max_length=512)[0]
    probs = {r["label"].lower(): r["score"] for r in results}

    score = probs["positive"] - probs["negative"]  # -1 to +1
    label = max(probs, key=probs.get)  # whichever class won

    return {
        "label": label,
        "score": round(score, 4)
    }


def analyse_batch(headlines: list) -> list:
    return [analyse_sentiment(h["title"]) for h in headlines]