const API_PATH = '/api/interpret';

async function interpretLocally(words, context, fallbackError = null) {
  // Reed owns this deterministic fallback; load it lazily so this adapter can be integrated independently.
  const local = await import('./interpret.js');
  return { ...local.interpret(words, context), provider: 'local', ...(fallbackError ? { fallbackError } : {}) };
}

export async function interpret(words, context = {}, options = {}) {
  if (typeof words !== 'string' || !words.trim() || words.length > 2000) {
    return interpretLocally(words, context);
  }
  try {
    const response = await fetch(API_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ words }),
      signal: options.signal,
    });
    if (!response.ok) throw new Error('Interpreter proxy unavailable');
    const result = await response.json();
    if (!result || !result.profile || !Array.isArray(result.profile.topics) ||
        !(result.question === null || typeof result.question === 'string')) {
      throw new Error('Invalid interpreter response');
    }
    return { ...result, provider: 'remote' };
  } catch (error) {
    if (options.signal?.aborted || error?.name === 'AbortError') throw error;
    return interpretLocally(words, context, 'The interpreter could not connect. You can retry or use basic matching.');
  }
}
