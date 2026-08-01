"""Eval metrics for extraction quality."""

import re


def normalize_title(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def title_match(predicted: str, expected: str, threshold: float = 0.6) -> bool:
    p, e = normalize_title(predicted), normalize_title(expected)
    if p == e:
        return True
    if e in p or p in e:
        return True
    p_words = set(p.split())
    e_words = set(e.split())
    if not e_words:
        return False
    overlap = len(p_words & e_words) / len(e_words)
    return overlap >= threshold


def precision_recall(predicted_titles: list[str], expected_titles: list[str]) -> dict:
    if not predicted_titles and not expected_titles:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not predicted_titles:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    if not expected_titles:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    matched_pred = set()
    matched_exp = set()
    for i, pred in enumerate(predicted_titles):
        for j, exp in enumerate(expected_titles):
            if j in matched_exp:
                continue
            if title_match(pred, exp):
                matched_pred.add(i)
                matched_exp.add(j)
                break

    precision = len(matched_pred) / len(predicted_titles)
    recall = len(matched_exp) / len(expected_titles)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


def grounding_rate(items: list[dict], transcript: str) -> float:
    if not items:
        return 1.0
    grounded = sum(1 for i in items if i.get("grounded"))
    return round(grounded / len(items), 3)
