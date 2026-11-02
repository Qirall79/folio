import re

from llama_index.core.node_parser.text.utils import split_by_sentence_tokenizer


def split_text_preserving_lists(text: str) -> list[str]:
    """Keep common PDF lists with their introduction; split other prose normally."""
    split_sentences = split_by_sentence_tokenizer()
    units = []
    block = []
    in_list = False

    def flush():
        if block:
            content = "\n".join(block)
            units.extend([content] if in_list else split_sentences(content))
            block.clear()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        is_item = bool(re.match(r"^(?:\d+[.)]|[•●▪■‣*–-])(?:\s+|$)", line))
        words = [word for word in line.split() if word[0].isalpha()]
        # Short lines with mostly capitalized words often mark a new section.
        is_heading = (
            not is_item
            and 0 < len(words) <= 10
            and not line.endswith((".", ",", ":", ";", "!", "?"))
            and sum(word[0].isupper() for word in words) > len(words) / 2
        )

        if is_heading or (
            in_list and not is_item and block[-1].endswith((".", "!", "?"))
        ):
            flush()
            in_list = False

        block.append(line)
        if is_item:
            in_list = True

    flush()
    return [unit.strip() for unit in units if unit.strip()]
