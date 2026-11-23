"""Evaluate profile selection against a small fictional, headline-only label set."""

import sys as _briefing_sys
from pathlib import Path as _BriefingPath
_briefing_sys.path.insert(0, str(_BriefingPath(__file__).resolve().parents[2]))
from dev.test_guard import enforce_test_environment as _enforce_test_environment
_enforce_test_environment()

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dev.ingest.core import rank_items


FIXTURE = Path(__file__).parent / 'fixtures' / 'interest-selection-candidates.json'


def main():
    dataset = json.loads(FIXTURE.read_text(encoding='utf-8'))
    items = dataset['items']
    assert len(items) == 40, f"expected about 40 candidates, found {len(items)}"
    assert all(not item.get('snippet') for item in items), 'fixture must remain headline-only'
    groups = {
        'payments_banking': ({
            'topics': [
                {'id': 'payments', 'label': 'Payments', 'scope': 'Payment rails, settlement, cards and transfers',
                 'include': ['payment', 'settlement', 'card', 'transfer', 'checkout', 'wallet'], 'exclude': [], 'mustFollow': False},
                {'id': 'banking', 'label': 'Banking', 'scope': 'Deposits, lending and financial stability',
                 'include': ['bank', 'deposit', 'lending', 'credit union', 'capital'], 'exclude': [], 'mustFollow': False},
            ]
        }, ['payments', 'banking']),
        'nc_health_insurance': ({
            'topics': [{'id': 'insurance', 'label': 'North Carolina health insurance policy',
                        'scope': 'North Carolina health insurance and policy',
                        'include': ['North Carolina', 'health', 'insurance', 'coverage', 'policy'],
                        'exclude': ['clinic', 'national health insurance survey'], 'mustFollow': False}]
        }, ['insurance']),
        'charlotte_housing': ({
            'topics': [{'id': 'custom-charlotte-housing', 'label': 'Charlotte housing',
                        'scope': 'Housing in Charlotte, North Carolina', 'include': ['Charlotte', 'housing'],
                        'exclude': ['space shuttle', 'CMS kindergarten', 'Brooklyn collapse'], 'mustFollow': False}]
        }, ['custom-charlotte-housing']),
        'upstate_housing': ({
            'topics': [{'id': 'custom-upstate-housing', 'label': 'Upstate housing',
                        'scope': 'Housing in Upstate South Carolina', 'include': ['Upstate', 'housing', 'Greenville', 'Spartanburg', 'Anderson'],
                        'exclude': ['mortgage calculator', 'school district', 'home tour'], 'mustFollow': False}]
        }, ['custom-upstate-housing']),
    }

    total_selected = 0
    total_correct = 0
    all_selected = set()
    for group, (profile, topic_ids) in groups.items():
        candidates = [item for item in items if item['group'] == group]
        selected = []
        for topic_id in topic_ids:
            selected.extend(rank_items(candidates, profile, topic_id))
        # The fixture has one intended profile per candidate; count each item once.
        selected = {item['id']: item for item in selected}.values()
        selected = list(selected)
        all_selected.update(item['id'] for item in selected)
        correct = sum(item['label'] in {'lead', 'quick_hit'} for item in selected)
        precision = correct / len(selected) if selected else 1.0
        total_selected += len(selected)
        total_correct += correct
        print(f"{group}: {correct}/{len(selected)} selected relevant ({precision:.0%} precision)")
        false_positives = [item['id'] for item in selected if item['label'] == 'exclude']
        assert not false_positives, f'{group}: selected labeled exclusions {false_positives}'

    precision = total_correct / total_selected if total_selected else 1.0
    print(f"overall: {total_correct}/{total_selected} selected relevant ({precision:.0%} precision)")
    assert total_selected >= 28, 'selector is too restrictive for this labeled set'
    assert precision >= 0.90, f'precision below 90%: {precision:.0%}'
    assert not all_selected.intersection({'ch-07', 'ch-08', 'ch-09'}), 'known housing distractor selected'


class InterestSelectionPrecisionTests(unittest.TestCase):
    def test_fictional_headline_selection_precision(self):
        main()


if __name__ == '__main__':
    main()
