"""Bibliographic discovery, excluding abstracts and full text even when open access."""
from urllib.parse import quote
from .base import Capability, DomainAdapter, clean
from ..safety import canonical_url


def licence_urls(records):
    return tuple(dict.fromkeys(url for row in (records or []) if isinstance(row, dict)
                               if (url := canonical_url(row.get('URL')))))


class EuropePmcAdapter(DomainAdapter):
    capability = Capability('europe_pmc', 'https://www.ebi.ac.uk/europepmc/webservices/rest/search',
                            ('biomedical', 'clinical'))

    def _search(self, query, count, freshness, budget):
        data = self.get({'query': query, 'format': 'json', 'resultType': 'core', 'pageSize': count}, budget)
        def convert(row, rank):
            source, ident = row['source'], row['id']
            types = row.get('pubTypeList', {}).get('pubType', [])
            corrections = tuple(clean(x) for x in types if 'correct' in x.lower() or 'errat' in x.lower())
            return self.row(f'https://europepmc.org/article/{quote(source, safe="")}/{quote(ident, safe="")}',
                            row['title'], rank, f'{source}:{ident}',
                            'Record licence: ' + (clean(row.get('license')) or 'unknown') +
                            '; abstract and full-text rights are separate; open access is not blanket permission.',
                            row.get('firstPublicationDate'), version=clean(row.get('versionNumber')),
                            updated_at=clean(row.get('dateOfRevision')),
                            retracted=True if 'Retracted Publication' in types else None, corrections=corrections)
        return self.normalize(data['resultList']['result'], convert, count)


class OpenAlexAdapter(DomainAdapter):
    capability = Capability('openalex', 'https://api.openalex.org/works', ('research',))

    def _search(self, query, count, freshness, budget):
        params = {'search': query, 'per-page': count}
        if self.api_key:
            params['api_key'] = self.api_key
        data = self.get(params, budget)
        def convert(row, rank):
            location = row.get('primary_location') or {}
            return self.row(row['id'], row['display_name'], rank, row['id'],
                            'OpenAlex metadata: CC0. Work licence: ' + (clean(location.get('license')) or 'unknown') +
                            '; metadata licence does not cover the paper or abstract.', row.get('publication_date'),
                            version=clean(location.get('version')), updated_at=clean(row.get('updated_date')),
                            retracted=row.get('is_retracted') if type(row.get('is_retracted')) is bool else None)
        return self.normalize(data['results'], convert, count)


class CrossrefAdapter(DomainAdapter):
    capability = Capability('crossref', 'https://api.crossref.org/works', ('research',))

    def _search(self, query, count, freshness, budget):
        params = {'query': query, 'rows': count}
        if self.contact:
            params['mailto'] = self.contact
        data = self.get(params, budget)
        def convert(row, rank):
            parts = row.get('published', {}).get('date-parts', [[]])[0]
            # Do not invent January 1 for year-only records.
            published = '-'.join(str(x).zfill(2) for x in parts) if len(parts) == 3 else None
            changes = tuple(clean(f"{x.get('type', 'update')}: {x.get('DOI', '')}")
                            for x in row.get('update-to', []) if isinstance(x, dict))
            return self.row('https://doi.org/' + quote(row['DOI'], safe='/'), row['title'][0], rank,
                            row['DOI'], 'Crossref bibliographic metadata; listed licences apply to specified content/versions only.',
                            published, license_urls=licence_urls(row.get('license')),
                            publication_date='-'.join(str(x).zfill(2) for x in parts) or None,
                            updated_at=clean(row.get('indexed', {}).get('date-time')), corrections=changes,
                            retracted=None)
        return self.normalize(data['message']['items'], convert, count)
