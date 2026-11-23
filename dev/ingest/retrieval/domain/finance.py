"""Filings and statistics discovery; never retain numeric series observations."""
import re
from urllib.parse import quote
from .base import Capability, DomainAdapter, clean
from ..provider import ProviderUnavailable


class EdgarAdapter(DomainAdapter):
    capability = Capability('edgar', 'https://efts.sec.gov/LATEST/search-index',
                            ('filings', 'securities'), key_requirement=None)

    def _search(self, query, count, freshness, budget):
        data = self.get({'q': query, 'dateRange': 'all', 'from': 0, 'size': count}, budget)
        def convert(hit, rank):
            row = hit['_source']
            cik = str(row['ciks'][0])
            accession = row.get('adsh') or str(hit['_id']).split(':')[0]
            if not cik.isdigit() or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession):
                return None
            title = f"{', '.join(row.get('display_names') or ['SEC filing'])} — {row.get('form', 'filing')}"
            url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/{accession}-index.html'
            return self.row(url, title, rank, accession,
                            'SEC access is not a licence to reuse issuer filings; third-party rights may apply.',
                            row.get('file_date'), jurisdiction='US', version=accession,
                            corrections=('amended filing',) if str(row.get('form', '')).endswith('/A') else ())
        return self.normalize(data['hits']['hits'], convert, count)


class FredAdapter(DomainAdapter):
    capability = Capability('fred', 'https://api.stlouisfed.org/fred/series/search',
                            ('economics',), key_requirement='FRED API key (free registration)')

    def _search(self, query, count, freshness, budget):
        data = self.get({'search_text': query, 'api_key': self.api_key, 'file_type': 'json',
                         'limit': count}, budget)
        return self.normalize(data['seriess'], lambda row, rank: self.row(
            'https://fred.stlouisfed.org/series/' + quote(row['id'], safe=''), row['title'], rank,
            row['id'], 'FRED/underlying owner terms apply; no storage or model use cleared. Link discovery only.',
            units=clean(row.get('units')), updated_at=clean(row.get('last_updated')),
            version=clean(row.get('realtime_start')), model_use='prohibited by this adapter'), count)


# Explicit series mappings prevent a general topic from silently choosing a geography/unit.
BLS_SERIES = {
    'LNS14000000': ('US unemployment rate (seasonally adjusted)', 'percent'),
    'CUUR0000SA0': ('US city average CPI-U, all items (not seasonally adjusted)', 'index, 1982–84=100'),
}


class BlsAdapter(DomainAdapter):
    capability = Capability('bls', 'https://api.bls.gov/publicAPI/v1/timeseries/data/',
                            ('labour', 'inflation'), minimum_interval_seconds=3)

    def _search(self, query, count, freshness, budget):
        match = re.search(r'\bbls:([A-Z0-9]+)\b', query, re.I)
        if not match:
            raise ProviderUnavailable('explicit BLS series required')
        series = match.group(1).upper()
        if series not in BLS_SERIES:
            raise ProviderUnavailable('unsupported BLS series')
        data = self.get({}, budget, endpoint=self.capability.endpoint + series)
        if data.get('status') != 'REQUEST_SUCCEEDED':
            raise ProviderUnavailable('BLS request rejected')
        title, units = BLS_SERIES[series]
        def convert(row, rank):
            if row.get('seriesID') != series:
                return None
            # Observations and footnotes remain in the response only; no values copied.
            return self.row('https://data.bls.gov/timeseries/' + series, title, rank, series,
                            'BLS-produced statistics generally public domain; cite BLS and check third-party material.',
                            units=units, jurisdiction='US')
        return self.normalize(data['Results']['series'], convert, count)
