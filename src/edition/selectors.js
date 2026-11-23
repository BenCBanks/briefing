import { visibleSections } from './edition.js';

export function revisionNumber(item) {
  const matches = [...String(item.revision_id || '').matchAll(/(?:^|[-._])r(\d+)/gi)];
  return matches.length ? Number(matches[matches.length - 1][1]) : 0;
}

export function revisionDate(item) {
  return String(item.published_at || item.reviewed_at || item.local_date || item.week_start || item.created_at || '');
}

export function displayDate(value) {
  const date = new Date(`${String(value || '').slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? String(value || '') : new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
}
export function plainDate(value) {
  const date = new Date(`${String(value || '').slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(date);
}
export function briefingDate(value) {
  const date = new Date(`${String(value || '').slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' }).format(date);
}


export function revisionGroup(items, id, predicate = () => true) {
  const candidates = items.filter(item => item.id === id && predicate(item));
  if (!candidates.length) return { candidates: [], current: [] };
  const superseded = new Set(candidates.flatMap(item => (item.corrections || []).map(correction => correction.previous_revision_id).filter(Boolean)));
  const current = candidates.filter(item => !superseded.has(item.revision_id) &&
    !['superseded', 'withdrawn'].includes(item.status) && item.superseded !== true && item.withdrawn !== true &&
    !item.superseded_by && !item.withdrawn_at);
  return { candidates, current };
}

export function compareRevision(a, b) {
  return revisionDate(a).localeCompare(revisionDate(b)) ||
    String(a.generated_at || a.updated_at || a.created_at || '').localeCompare(String(b.generated_at || b.updated_at || b.created_at || '')) ||
    revisionNumber(a) - revisionNumber(b) || String(a.revision_id).localeCompare(String(b.revision_id));
}

export function currentRevision(items, id, predicate = () => true) {
  const group = revisionGroup(items, id, predicate);
  if (group.current.length === 1) return group.current[0];
  const sameGeneratedEdition = group.current.length > 1 && group.current.every(item =>
    item.generated === true && ['daily', 'weekly'].includes(item.kind)
  );
  return sameGeneratedEdition ? group.current.sort(compareRevision).at(-1) : null;
}

export function currentItems(items, predicate = () => true) {
  const groups = new Map();
  for (const item of items.filter(predicate)) {
    const type = item.kind || (item.topic_id ? 'deep-dive' : 'item');
    groups.set(`${type}\u0000${item.id}`, { id: item.id, type });
  }
  return [...groups.values()].map(({ id, type }) => currentRevision(items, id, item =>
    predicate(item) && (item.kind || (item.topic_id ? 'deep-dive' : 'item')) === type
  )).filter(Boolean);
}


export function firstReadableSection(edition) {
  return visibleSections(edition).find(section => section.kind !== 'link') || null;
}

export function dailyBriefings(editions = []) {
  const rows = currentItems(editions, edition => edition.kind === 'daily');
  const generated = rows.filter(edition => edition.generated === true);
  const available = generated.length ? generated : rows;
  // Live generation can publish more than one ID for the same local date.
  // Earlier briefings are date-based, so keep only the newest edition per day.
  const newestByDate = new Map();
  for (const edition of available) {
    const date = String(edition.local_date || revisionDate(edition)).slice(0, 10);
    const previous = newestByDate.get(date);
    if (!previous || compareRevision(edition, previous) > 0) newestByDate.set(date, edition);
  }
  return [...newestByDate.values()].sort((a, b) => revisionDate(b).localeCompare(revisionDate(a)));
}


export function latestGeneratedDaily(editions = []) {
  return editions.filter(item => item?.kind === 'daily' && item.generated === true)
    .sort(compareRevision).at(-1) || null;
}

export function newerEdition(candidate, previous) {
  if (!candidate) return false;
  if (!previous) return true;
  const candidateDate = String(candidate.local_date || '').slice(0, 10);
  const previousDate = String(previous.local_date || '').slice(0, 10);
  if (candidateDate && previousDate && candidateDate !== previousDate) return candidateDate > previousDate;
  const candidateTime = Date.parse(candidate.generated_at || candidate.updated_at || '');
  const previousTime = Date.parse(previous.generated_at || previous.updated_at || '');
  if (Number.isFinite(candidateTime) && Number.isFinite(previousTime) && candidateTime !== previousTime) return candidateTime > previousTime;
  return candidate.id !== previous.id || candidate.revision_id !== previous.revision_id;
}

export function includeNewerCachedEdition(data, previousPack, serverProfileChanged) {
  if (!previousPack || serverProfileChanged) return data;
  const cached = latestGeneratedDaily(previousPack.editions);
  const server = latestGeneratedDaily(data.editions);
  if (!cached || !server) return data;
  const cachedDate = String(cached.local_date || '').slice(0, 10);
  const serverDate = String(server.local_date || '').slice(0, 10);
  const cachedTime = Date.parse(cached.generated_at || cached.updated_at || '');
  const serverTime = Date.parse(server.generated_at || server.updated_at || '');
  const cacheIsAhead = cachedDate && serverDate && cachedDate > serverDate ||
    cachedDate === serverDate && Number.isFinite(cachedTime) && Number.isFinite(serverTime) && cachedTime > serverTime;
  if (!cacheIsAhead || data.editions.some(item => item.id === cached.id && item.revision_id === cached.revision_id)) return data;
  const sources = [...new Map([...data.sources, ...previousPack.sources].map(source => [source.id, source])).values()];
  return { ...data, sources, editions: [...data.editions, cached] };
}

