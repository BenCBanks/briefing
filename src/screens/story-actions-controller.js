export function createStoryActionsController(app) {
  function showWhySheet(section) {
    const topicId = section.topic_ids?.[0];
    const topic = app.pack.topics.find(item => item.id === topicId);
    const matched = app.interestProfile?.topics.find(item => item.id === topicId);
    const dialog = app.node('dialog', null, 'why-story-dialog');
    const sheet = app.node('section', null, 'why-story-sheet');
    const close = app.node('button', 'Close', 'why-story-close'); close.type = 'button'; close.setAttribute('aria-label', 'Close Why this story');
    close.addEventListener('click', () => dialog.close());
    const heading = app.node('h2', 'Why this story?');
    heading.id = 'why-story-heading';
    dialog.setAttribute('aria-labelledby', heading.id);
    sheet.append(close, app.node('p', 'WHY THIS STORY', 'eyebrow'), heading);
    sheet.append(app.node('p', matched ? `You follow ${matched.label.toLowerCase()} because you wrote “${matched.sourcePhrase}”.` : `This story is filed under ${topic?.label || 'your interests'}.`));
    sheet.append(app.node('p', topic?.scope || '', 'why-story-scope'));
    const actionsRow = app.node('div', null, 'why-story-actions');
    const more = app.node('button', 'More like this'); more.type = 'button';
    more.addEventListener('click', () => { if (topicId) app.recordSignal('more', section.id, topicId); dialog.close(); });
    const less = app.node('button', 'Less like this'); less.type = 'button';
    less.addEventListener('click', () => { if (topicId) app.recordSignal('less', section.id, topicId); dialog.close(); });
    const mute = app.node('button', 'Not interested in this topic'); mute.type = 'button';
    const confirm = app.node('div', null, 'why-story-confirm'); confirm.hidden = true;
    confirm.append(app.node('p', `Mute ${topic?.label || 'this topic'}? It will be removed from your interests and stay muted until you add it back.`));
    const confirmMute = app.node('button', 'Confirm mute', 'primary'); confirmMute.type = 'button';
    confirmMute.addEventListener('click', () => {
      if (topicId && topic) {
        app.recordSignal('mute', section.id, topicId);
        if (!app.interestProfile.muted.some(item => item.label === topic.label)) app.interestProfile.muted.push({ label: topic.label });
        app.interestProfile.topics = app.interestProfile.topics.filter(item => item.id !== topicId);
        app.interestProfile = app.saveProfile(app.interestProfile);
        app.invalidateTopicsEdition();
        void app.startBuildGeneration(app.interestProfile, null, { inline: true });
      }
      dialog.close();
    });
    const cancelMute = app.node('button', 'Keep this topic'); cancelMute.type = 'button';
    cancelMute.addEventListener('click', () => { confirm.hidden = true; actionsRow.hidden = false; });
    confirm.append(confirmMute, cancelMute);
    mute.addEventListener('click', () => { confirm.hidden = false; actionsRow.hidden = true; confirmMute.focus(); });
    const edit = app.node('button', 'Edit my interests'); edit.type = 'button';
    edit.addEventListener('click', () => {
      dialog.close();
      const current = app.location.hash || '#today';
      app.navigate('#interests', { root: app.rootForHash(current), parentHash: current, parentScroll: app.window.scrollY, parentFocus: app.focusToken() });
    });
    actionsRow.append(more, less, mute); sheet.append(actionsRow, confirm, edit); dialog.append(sheet);
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); }, { once: true });
    app.document.body.append(dialog);
    dialog.addEventListener('close', () => dialog.remove(), { once: true });
    dialog.showModal();
  }

  function showStoryActionsSheet(section, edition, trigger) {
    const dialog = app.node('dialog', null, 'story-actions-dialog');
    const sheet = app.node('section', null, 'story-actions-sheet');
    const close = app.node('button', 'Close', 'story-actions-close'); close.type = 'button'; close.setAttribute('aria-label', 'Close story actions');
    close.addEventListener('click', () => dialog.close());
    const heading = app.node('h2', 'Share this story'); heading.id = 'story-actions-heading';
    dialog.setAttribute('aria-labelledby', heading.id);
    sheet.append(close, app.node('p', 'STORY', 'eyebrow'), heading, app.node('p', section.heading, 'story-actions-title'));

    const status = app.node('p', '', 'story-actions-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const storyUrl = app.location.href;
    const copyText = async text => {
      if (app.navigator.clipboard?.writeText) { await app.navigator.clipboard.writeText(text); return; }
      const field = app.node('textarea'); field.value = text; field.setAttribute('readonly', '');
      field.style.position = 'fixed'; field.style.opacity = '0'; app.document.body.append(field); field.select();
      const copied = app.document.execCommand?.('copy'); field.remove();
      if (!copied) throw new Error('clipboard_unavailable');
    };
    const share = app.node('button', 'Share story', 'primary story-share-action'); share.type = 'button';
    share.addEventListener('click', async () => {
      if (typeof app.navigator.share === 'function') {
        try { await app.navigator.share({ title: section.heading, text: 'A story from my daily briefing.', url: storyUrl }); app.showToast('Share options opened.'); }
        catch (error) { if (error?.name !== 'AbortError') status.textContent = 'Sharing is unavailable right now.'; }
        return;
      }
      try { await copyText(storyUrl); app.showToast('Sharing is unavailable. Story link copied instead.'); }
      catch { status.textContent = 'Sharing and copying are unavailable in this browser.'; }
    });
    const copyLink = app.node('button', 'Copy link', 'story-copy-link'); copyLink.type = 'button';
    copyLink.addEventListener('click', async () => {
      try { await copyText(storyUrl); app.showToast('Story link copied.'); }
      catch { status.textContent = 'Copy is unavailable. You can copy the page address from your browser.'; }
    });
    const shareActions = app.node('div', null, 'story-share-actions'); shareActions.append(share, copyLink); sheet.append(shareActions, status);

    const report = app.node('section', null, 'story-report-form');
    report.append(app.node('h3', 'Report a mistake'));
    report.append(app.node('p', 'What should we check? Nothing is sent automatically; copy the details into your message to the editor.', 'story-report-note'));
    const label = app.node('label', 'What looks wrong?', 'story-report-label'); label.htmlFor = 'story-report-note';
    const note = app.node('textarea'); note.id = 'story-report-note'; note.name = 'report'; note.rows = 3; note.maxLength = 1200;
    note.placeholder = 'A date, name, number, or other detail to check';
    const copyReport = app.node('button', 'Copy report details', 'story-copy-report'); copyReport.type = 'button';
    copyReport.addEventListener('click', async () => {
      if (!note.value.trim()) { status.textContent = 'Add a short note about what to check first.'; note.focus(); return; }
      const reportText = `Story: ${section.heading}\nLink: ${storyUrl}\n\nWhat to check:\n${note.value.trim()}`;
      try { await copyText(reportText); app.showToast('Report details copied. Paste them into your message to the editor.'); }
      catch { status.textContent = 'Copy is unavailable. Your note is still here; you can select and copy it.'; }
    });
    report.append(label, note, copyReport); sheet.append(report); dialog.append(sheet);
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    app.document.body.append(dialog);
    dialog.addEventListener('close', () => { dialog.remove(); if (trigger?.isConnected) trigger.focus({ preventScroll: true }); }, { once: true });
    dialog.showModal();
  }

  return { showWhySheet, showStoryActionsSheet };
}
