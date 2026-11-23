"""Wikipedia page discovery with revision identifiers; snippets are discarded."""
from .base import Capability, DomainAdapter, clean


class WikipediaAdapter(DomainAdapter):
    capability = Capability('wikipedia', 'https://en.wikipedia.org/w/api.php', ('background',))

    def _search(self, query, count, freshness, budget):
        data = self.get({'action': 'query', 'format': 'json', 'formatversion': 2,
                         'generator': 'search', 'gsrsearch': query, 'gsrlimit': count,
                         'prop': 'info|revisions', 'inprop': 'url', 'rvprop': 'ids|timestamp'}, budget)
        def convert(row, rank):
            revision = (row.get('revisions') or [{}])[0]
            return self.row(row['fullurl'], row['title'], rank, row['pageid'],
                            'Wikipedia text: CC BY-SA 4.0 (attribution/share-alike); media may have different licences.',
                            version=clean(revision.get('revid')), updated_at=clean(revision.get('timestamp')),
                            license_urls=('https://creativecommons.org/licenses/by-sa/4.0/',))
        return self.normalize(data.get('query', {}).get('pages', []), convert, count)
