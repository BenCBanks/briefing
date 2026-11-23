"""Shared episode lengths and labelled pre-start estimates from the usage price list."""
from decimal import Decimal, ROUND_CEILING

LENGTHS = (3, 5, 10, 15)
WORDS_PER_MINUTE = 140
BUDGET_MESSAGE = "Today's budget is used. Try tomorrow."


def length_policy(minutes):
    if type(minutes) is not int or minutes not in LENGTHS:
        raise ValueError('Length must be 3, 5, 10, or 15 minutes.')
    index = LENGTHS.index(minutes)
    return {'minutes': minutes, 'target_words': minutes * WORDS_PER_MINUTE,
            'question_count': (3, 4, 4, 5)[index], 'max_rounds': (2, 2, 3, 3)[index],
            'max_reads': (12, 12, 16, 20)[index], 'grounded_queries': (1, 2, 3, 4)[index]}


def estimate_episode(minutes, ledger, model, *, include_tts=True):
    policy = length_policy(minutes)
    # Planning, chapter writing, one capped expansion per short chapter,
    # complete-script verification and one repair.
    # Evidence size and low-thinking output are assumptions, never an invoice.
    prompt_tokens = 9000 + minutes * 1600
    chapter_target = (policy['target_words'] + policy['question_count'] - 1) // policy['question_count']
    expansion_output = policy['question_count'] * (512 + chapter_target * 4)
    output_tokens = 2000 + minutes * 700 + expansion_output
    input_rate, output_rate = ledger._text_rates(model)
    text = (prompt_tokens * input_rate + output_tokens * output_rate) / Decimal(1_000_000)
    search = policy['grounded_queries'] * ledger.config['_search_prices']['gemini']
    chars = policy['target_words'] * 6
    tts = (Decimal(chars) * ledger.config['_tts_char_rate'] / Decimal(1_000_000)
           + Decimal(minutes) * ledger.config['_tts_minute_rate']) if include_tts else Decimal(0)
    total = text + search + tts
    cents = int((total * 100).to_integral_value(rounding=ROUND_CEILING))
    return {'usd': f'{total:.8f}', 'label': f'about {cents} cents', 'kind': 'estimate',
            'model': model, 'thinking': 'low',
            'tts_model': 'gemini-3.8-flash-lite-tts' if include_tts else None,
            'research_usd': f'{search:.8f}', 'text_usd': f'{text:.8f}', 'tts_usd': f'{tts:.8f}',
            'basis': ('Usage price list; bounded grounding, estimated text tokens including low thinking, '
                      'six characters per spoken word and requested TTS minutes. Actual usage may differ.'
                      if include_tts else
                      'Usage price list; bounded grounding and estimated text tokens including low thinking. '
                      'TTS is disabled. Actual usage may differ.')}
