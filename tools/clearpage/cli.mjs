// The app's way into Clearpage (https://github.com/wowanirudh-boop/webscraper): one page in, one JSON line out.
// tools/build_clearpage.py bundles this, with the extractor it imports, into
// devils_workshop/vendor/clearpage/clearpage.cjs, which the app runs with its own copy of Node.
//
//   node clearpage.cjs <url>                 the article at <url>, all its pages joined
//   node clearpage.cjs --file <page> <url>   a page saved on disk, read as if it came from <url> (no network)
//
// Prints {"ok": true, "title", "url", "markdown", "words", "pages", "author", "site", "notice"}
// or {"ok": false, "error"} (the extractor's own message, written for people).
import { readFile } from 'node:fs/promises';
import { extractHtml, extractUrl } from './src/extract.mjs';

async function main() {
  const args = process.argv.slice(2);
  try {
    const result = args[0] === '--file'
      ? { ...extractHtml(await readFile(args[1], 'utf8'), args[2]), pageCount: 1 }
      : await extractUrl(args[0]);
    process.stdout.write(JSON.stringify({
      ok: true, title: result.title, url: result.url, markdown: result.markdown, words: result.words,
      pages: result.pageCount || 1, author: result.author || '', site: result.siteName || '', notice: result.notice || ''
    }));
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: error?.message || String(error) }));
    process.exitCode = 1;
  }
}

main();
