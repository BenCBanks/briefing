function svg(name, size, viewBox, pathData) {
  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('class', `icon icon-${name}`);
  icon.setAttribute('width', String(size));
  icon.setAttribute('height', String(size));
  icon.setAttribute('viewBox', viewBox);
  icon.setAttribute('fill', 'currentColor');
  icon.setAttribute('aria-hidden', 'true');
  icon.setAttribute('focusable', 'false');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', pathData);
  icon.append(path);
  return icon;
}

export function createPlayIcon(size = 14) {
  return svg('play', size, '0 0 24 24', 'M7 4.5v15a1 1 0 0 0 1.5.9l12-7.5a1 1 0 0 0 0-1.8l-12-7.5A1 1 0 0 0 7 4.5Z');
}

export function createPauseIcon(size = 14) {
  const icon = svg('pause', size, '0 0 24 24', '');
  icon.replaceChildren();
  for (const x of [6, 13.5]) {
    const bar = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bar.setAttribute('x', String(x)); bar.setAttribute('y', '4'); bar.setAttribute('width', '4.5');
    bar.setAttribute('height', '16'); bar.setAttribute('rx', '1.2');
    icon.append(bar);
  }
  return icon;
}
