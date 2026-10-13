"""Context-tag helpers shared by the engine and any module that provides game context."""
from collections import Counter

from .settings import TAG_MIN_SHARE


def dominant_tag(samples, now, span, min_share=TAG_MIN_SHARE):
    """Tag held for >= min_share of the last `span` seconds; '' if mixed (a transition) or no data.

    samples: iterable of tuples whose first item is a time and second item the tag."""
    recent = [s[1] for s in samples if s[0] >= now - span]
    if not recent:
        return ""
    tag, n = Counter(recent).most_common(1)[0]
    return tag if n / len(recent) >= min_share else ""
