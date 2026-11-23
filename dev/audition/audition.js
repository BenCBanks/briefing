const status = document.querySelector('.status');
let selectedLetter = 'A';

function markSelected(letter) {
  if (!/^[A-F]$/.test(letter || '')) return;
  selectedLetter = letter;
  for (const card of document.querySelectorAll('.sample')) {
    const button = card.querySelector('.choose-voice');
    const selected = card.dataset.label === selectedLetter;
    button.setAttribute('aria-pressed', String(selected));
    button.setAttribute('aria-label', selected ? `Sample ${card.dataset.label} voice selected` :
      `Use sample ${card.dataset.label} voice`);
    button.textContent = selected ? 'Using this voice' : 'Use this voice';
    button.disabled = selected;
  }
}

fetch('/api/voice', { cache: 'no-store' })
  .then(response => { if (!response.ok) throw new Error(); return response.json(); })
  .then(selection => markSelected(selection.letter))
  .catch(() => { status.textContent = 'Choose a sample to set the voice for new episodes.'; });

fetch('/audition-audio/manifest.json', { cache: 'no-store' })
  .then(response => {
    if (!response.ok) throw new Error('Audition files are not ready yet.');
    return response.json();
  })
  .then(manifest => {
    const samples = new Map((manifest.samples || []).map(sample => [sample.label, sample]));
    for (const card of document.querySelectorAll('.sample')) {
      const sample = samples.get(card.dataset.label);
      const audio = card.querySelector('audio');
      if (sample && /^[A-F]$/.test(sample.label) && /^[a-z0-9-]+\.(?:mp3|wav)$/.test(sample.file)) {
        audio.src = `/audition-audio/${sample.file}`;
      } else {
        audio.remove();
        card.insertAdjacentHTML('beforeend', '<p>Sample unavailable.</p>');
        card.querySelector('.choose-voice').disabled = true;
      }
      card.querySelector('.choose-voice').addEventListener('click', async () => {
        const letter = card.dataset.label;
        const button = card.querySelector('.choose-voice');
        button.disabled = true;
        status.textContent = `Saving sample ${letter}…`;
        try {
          const response = await fetch('/api/voice', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ letter }),
          });
          if (!response.ok) throw new Error();
          const saved = await response.json();
          markSelected(saved.letter);
          status.textContent = `Sample ${saved.letter} will be used for new episode audio.`;
        } catch {
          button.disabled = false;
          status.textContent = 'Voice choice could not be saved. Try again.';
        }
      });
    }
    if (!status.textContent) status.textContent = 'Six samples use the same fictional passage.';
  })
  .catch(error => { status.textContent = error.message; });
