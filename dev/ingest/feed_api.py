"""Bounded query parsing for the private preview feed route."""

import json
import re

from .core import feed_items, load_registry


def feed_response(query, get_items=feed_items):
    topic = query.get('topic', [''])[0].strip()
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', topic) or len(topic) > 64:
        return 400, {'error': 'invalid_topic'}
    encoded_profile = query.get('profile', [None])[0]
    if encoded_profile is None:
        profile = {'topics': [{'id': topic, 'label': topic.replace('-', ' '), 'scope': '',
                               'include': [], 'exclude': [], 'mustFollow': False}], 'muted': []}
    else:
        if len(encoded_profile) > 8192:
            return 413, {'error': 'profile_too_large'}
        try:
            profile = json.loads(encoded_profile)
        except (ValueError, TypeError):
            return 400, {'error': 'invalid_profile'}
        if not isinstance(profile, dict) or not isinstance(profile.get('topics'), list) or len(profile['topics']) > 12:
            return 400, {'error': 'invalid_profile'}
    try:
        if get_items is feed_items:
            items = get_items(topic, profile, registry=load_registry())
        else:
            items = get_items(topic, profile)
    except (OSError, ValueError, TypeError):
        return 500, {'error': 'feed_unavailable'}
    return 200, {'topic': topic, 'items': items}
