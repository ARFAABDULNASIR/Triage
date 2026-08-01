import re

# Lines that are never tickets
CHATTER_PHRASES = (
    "thanks for joining", "thank you for", "good morning", "good afternoon",
    "hello everyone", "how are you", "nice to meet", "welcome to",
    "sounds good", "makes sense", "good point", "i agree", "yeah okay",
    "let me know what you think", "any questions", "anything else",
    "can you hear me", "you're on mute", "can everyone see",
    "great meeting", "talk soon", "catch up later", "have a good",
)

# Weak patterns — alone not enough for a ticket
WEAK_ONLY = re.compile(
    r"^(?:yeah|yes|no|ok|okay|sure|right|exactly|totally|interesting|cool|hmm|uh|um)\b",
    re.I,
)

# Strong commitment / assignment signals
STRONG_ACTION = re.compile(
    r"\b(?:"
    r"i(?:'ll| will)\s+\w+|we(?:'ll| will)\s+\w+|"
    r"(?:need|has|have) to\s+\w+|must\s+\w+|going to\s+\w+|"
    r"(?:can|could) you\s+(?:please\s+)?(?:send|share|fix|build|create|update|review|schedule|"
    r"prepare|complete|finish|deliver|write|draft|ship|deploy|test|investigate|resolve|"
    r"implement|assign|follow up|follow-up|book|confirm|migrate|integrate)|"
    r"(?:action item|follow[- ]?up|todo|to-do|deliverable|next step).{5,}|"
    r"(?:assigned to|owner is|responsible for|taking the action)|"
    r"(?:by|before)\s+(?:monday|tuesday|wednesday|thursday|friday|tomorrow|eod|end of day|next week)"
    r")\b",
    re.I,
)

IMPERATIVE_START = re.compile(
    r"^(?:send|share|fix|build|create|update|review|schedule|call|email|draft|ship|deploy|"
    r"test|write|prepare|complete|finish|deliver|follow up|follow-up|set up|setup|"
    r"investigate|resolve|implement|design|approve|confirm|book|assign|document|migrate|"
    r"integrate|add|remove|refactor|merge|release|publish|coordinate|organize|plan)\b",
    re.I,
)

WORK_CONTEXT = re.compile(
    r"\b(?:doc|document|report|api|bug|fix|feature|ticket|pr|pull request|demo|deck|"
    r"slide|email|client|customer|team|sprint|release|deploy|test|qa|design|spec|"
    r"proposal|contract|invoice|meeting|schedule|deadline|launch|migration|integration|"
    r"code|repo|database|server|endpoint|ui|ux|mockup|wireframe|budget|roadmap)\b",
    re.I,
)

SPEAKER_LINE = re.compile(
    r"^(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?([A-Za-z][A-Za-z\s.'-]{0,30}):\s*(.+)$"
)


def is_chatter_line(text: str) -> bool:
    lower = text.lower().strip()
    if len(lower) < 12:
        return True
    if WEAK_ONLY.match(lower):
        return True
    if any(p in lower for p in CHATTER_PHRASES):
        return True
    # pure questions with no task
    if lower.endswith("?") and not STRONG_ACTION.search(text):
        return True
    return False


def is_actionable_line(text: str) -> bool:
    if is_chatter_line(text):
        return False
    if not STRONG_ACTION.search(text):
        return False
    # need some substance — work context OR long enough commitment
    words = text.split()
    if len(words) < 6 and not WORK_CONTEXT.search(text):
        return False
    return True


def is_valid_ticket(item: dict) -> bool:
    title = (item.get("title") or "").strip()
    quote = (item.get("source_quote") or "").strip()
    if len(title) < 10 or len(title.split()) < 3:
        return False
    if title.endswith("...") or title.endswith("…"):
        return False
    if "?" in title.rstrip("?"):
        return False
    lower_title = title.lower()
    if any(p in lower_title for p in CHATTER_PHRASES):
        return False
    if not IMPERATIVE_START.match(title):
        return False
    if quote and is_chatter_line(quote) and not STRONG_ACTION.search(quote):
        return False
    return True


def prefilter_transcript(text: str) -> str:
    """Drop obvious non-work lines before sending to the API — faster + cleaner."""
    kept = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = SPEAKER_LINE.match(line)
        content = m.group(2) if m else line
        if is_chatter_line(content):
            continue
        if len(content) < 15 and not STRONG_ACTION.search(content):
            continue
        kept.append(line)
    return "\n".join(kept) if kept else text.strip()
