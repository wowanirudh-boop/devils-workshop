// Preloaded before the app's bundled extractor: a fake website whose story is split over three numbered pages
// (https://tales.example.com/story?page=1..3), plus the DNS answer the extractor checks first. Logs each page
// the extractor asks for to stderr.
const para = (n, k) => `<p>PAGE-${n}-PARA-${k} The keeper climbed the iron steps again and counted them under her breath, ` +
  `as her father had taught her long ago, and the lamp waited dark above her in the cold.</p>`;
const pager = n => `<nav class="pagination">${[1, 2, 3].map(i => `<a href="/story?page=${i}">${i}</a>`).join(' ')}` +
  (n < 3 ? ` <a rel="next" href="/story?page=${n + 1}">Next</a>` : '') + `</nav>`;
const page = n => `<html><head><title>The Long Stair</title></head><body><article><h1>The Long Stair</h1>` +
  `${[1, 2, 3].map(k => para(n, k)).join('')}</article>${pager(n)}</body></html>`;

globalThis.fetch = async (input) => {
  const url = new URL(typeof input === 'string' ? input : input.url);
  if (url.hostname === 'cloudflare-dns.com') {
    const type = url.searchParams.get('type');
    const answer = type === 'A' ? [{ type: 1, data: '93.184.216.34' }] : [];
    return new Response(JSON.stringify({ Answer: answer }), { headers: { 'content-type': 'application/dns-json' } });
  }
  const n = Number(url.searchParams.get('page') || 1);
  process.stderr.write(`fetched page ${n}\n`);
  return new Response(page(n), { headers: { 'content-type': 'text/html; charset=utf-8' } });
};
