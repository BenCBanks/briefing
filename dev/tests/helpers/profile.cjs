const DEFAULT_PROFILE = {
  version: 1,
  words: 'Follow payments every day.',
  minutes: 12,
  background: null,
  muted: [],
  topics: [{
    id: 'payments', label: 'Payments', scope: 'Payments and settlements',
    include: ['payments'], exclude: [], depth: 'standard', cadence: 'daily',
    mustFollow: false, sourcePhrase: 'payments',
  }],
};

function builtProfileKey(profile) {
  return JSON.stringify({
    words: profile?.words || '',
    minutes: profile?.minutes || 12,
    topics: (profile?.topics || []).map(topic => ({
      id: topic.id, include: topic.include, exclude: topic.exclude,
      depth: topic.depth, cadence: topic.cadence, mustFollow: topic.mustFollow,
      sourcePhrase: topic.sourcePhrase,
    })),
    muted: profile?.muted || [],
  });
}

async function seedInterestProfile(page, profile = DEFAULT_PROFILE) {
  await page.addInitScript(({ seededProfile, key }) => {
    localStorage.setItem('briefing:v1:interest-profile', JSON.stringify(seededProfile));
    localStorage.setItem('briefing:v1:built-profile-key', key);
  }, { seededProfile: profile, key: builtProfileKey(profile) });
}

module.exports = { DEFAULT_PROFILE, builtProfileKey, seedInterestProfile };
