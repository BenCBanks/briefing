"""Rebuild ten tiny, fictional, dependency-free quality fixtures."""
from pathlib import Path
import json
import zlib

ROOT = Path(__file__).resolve().parent


def pdf(pages, *, compressed=False):
    """Produce valid text PDFs with explicit page tree, font and xref offsets."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for lines in pages:
        page_id = len(objects) + 1
        kids.append(f'{page_id} 0 R')
        objects.append((f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
                        f'/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>').encode())
        content = b'BT /F1 12 Tf 50 730 Td 16 TL\n'
        for line in lines:
            escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            content += b'(' + escaped.encode('cp1252') + b') Tj T*\n'
        content += b'ET'
        if compressed:
            content = zlib.compress(content)
        dictionary = f'<< /Length {len(content)}' + (' /Filter /FlateDecode' if compressed else '') + ' >>'
        objects.append(dictionary.encode() + b'\nstream\n' + content + b'\nendstream')
    objects[1] = f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {len(kids)} >>'.encode()
    output = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for i, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += f'{i} 0 obj\n'.encode() + body + b'\nendobj\n'
    xref = len(output)
    output += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    output += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    output += f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return bytes(output)


def build():
    rows = []
    def add(name, body, expected, mime):
        (ROOT / name).write_bytes(body if isinstance(body, bytes) else body.encode())
        gold = name + '.txt'
        (ROOT / gold).write_text(expected + '\n')
        rows.append({'file': name, 'gold': gold, 'content_type': mime})
    title = 'Alderhaven opens a seed library'
    para = 'The fictional Alderhaven library will lend seed packets from Monday. Residents can borrow three packets and return seeds after the autumn harvest.'
    second = 'Volunteers will explain planting and storage during a free workshop. The pilot will run for six months before the town reviews participation.'
    gold = '\n'.join((title, para, second))
    add('article.html', f'<html><head><title>Publisher home</title></head><body><nav>News Sport Menu</nav><article><h1>{title}</h1><p>{para}</p><p>{second}</p></article><footer>Copyright fiction</footer></body></html>', gold, 'text/html')
    add('unmarked.html', f'<html><body><div class="sidebar"><p>Promotional offer and newsletter</p></div><div><h1>{title}</h1><p>{para}</p><p>{second}</p></div></body></html>', gold, 'text/html')
    add('noisy.html', f'<main><h1>{title}</h1><nav>{"<a href=\"https://example.invalid\">Browse related news</a> " * 60}</nav><p>{para}</p><div class="related-stories"><p>Unrelated railway story.</p></div><p hidden>Hidden sale</p><p>{second}</p><script>document.write("Fake result")</script><aside>Advertisement</aside></main>', gold, 'text/html')
    # Omitted closing paragraphs, entities and inline formatting are intentional.
    add('malformed.html', '<div class="entry-content"><h1>Fictional water &amp; gardens</h1><p>The <strong>Brookmere</strong> team measured water use in a community garden. Its trial used rain barrels for six weeks.<p>Volunteers recorded 240 litres saved, but the team has not tested a full summer.</div>', 'Fictional water & gardens\nThe Brookmere team measured water use in a community garden. Its trial used rain barrels for six weeks.\nVolunteers recorded 240 litres saved, but the team has not tested a full summer.', 'text/html')
    add('short.html', '<article><h1>Fictional ferry notice</h1><p>Morning sailings resume Friday.</p></article>', 'Fictional ferry notice\nMorning sailings resume Friday.', 'text/html')
    add('paragraph.html', '<p>Fictional Fernvale opens its new public reading room on Monday.</p>', 'Fictional Fernvale opens its new public reading room on Monday.', 'text/html')
    pages = [['Fictional Fernvale transport report', 'The trial added two morning buses to the valley route.', 'Passenger counts rose from 40 to 55 during the first week.']]
    add('report.pdf', pdf(pages), '\n'.join(pages[0]), 'application/pdf')
    add('compressed.pdf', pdf(pages, compressed=True), '\n'.join(pages[0]), 'application/pdf')
    pages = [['Fictional rain barrel trial', 'Brookmere collected 240 litres in six weeks.'], ['Trial limitations', 'These observations do not establish results for a full summer.']]
    add('multipage.pdf', pdf(pages), '\n\n'.join('\n'.join(p) for p in pages), 'application/pdf')
    pages = [['Fictional archive (revision 2)', 'The index records C:\\Fiction and a sample title (Gardens).', 'The catalogue contains 17 entries; none are real records.']]
    add('escaped.pdf', pdf(pages), '\n'.join(pages[0]), 'application/pdf')
    (ROOT / 'manifest.json').write_text(json.dumps(rows, indent=2) + '\n')


if __name__ == '__main__':
    build()
