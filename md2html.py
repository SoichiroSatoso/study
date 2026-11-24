"""Markdown -> HTML 変換（数式: KaTeX / 図: Mermaid）

使い方:
  python md2html.py                 # フォルダ内の .md を全部 HTML にしてブラウザで開く
  python md2html.py 20261123_SAT.md # 指定したファイルだけ
  python md2html.py --watch         # 保存するたびに自動で変換し、ブラウザも自動で更新
  python md2html.py --no-open       # ブラウザを開かない

外部ライブラリ不要（Python 標準ライブラリのみ）。
表示時に CDN から marked / KaTeX / Mermaid を読み込むので、ネット接続が必要。
"""

import argparse
import functools
import html
import json
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
EXCLUDE = {"AGENTS.md", "CLAUDE.md", "README.md"}
PORT = 8765

TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown-light.css">
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
  body { background: #f6f8fa; margin: 0; }
  .markdown-body { box-sizing: border-box; max-width: 900px; margin: 24px auto; padding: 40px 48px;
                   background: #fff; border: 1px solid #d0d7de; border-radius: 8px; }
  .markdown-body img { max-width: 100%; }
  .katex-display { overflow-x: auto; overflow-y: hidden; }
  .math-error { color: #c00; font-family: monospace; }
</style>
</head>
<body>
<article id="content" class="markdown-body"></article>
<script id="md-source" type="application/json">__SOURCE__</script>
<script>
(function () {
  const src = JSON.parse(document.getElementById("md-source").textContent);
  const store = [];
  const keep = (s) => { store.push(s); return "@@PH" + (store.length - 1) + "@@"; };

  // 1. コードブロック・インラインコードを退避（中の $ を数式扱いしないため）
  let text = src.replace(/^(```|~~~)[\s\S]*?^\1[ \t]*$/gm, (m) => keep({ raw: m }));
  text = text.replace(/`[^`\n]+`/g, (m) => keep({ raw: m }));

  // 2. 数式を退避（marked に \ や _ を壊されないため）
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (m, tex) => keep({ tex: tex, display: true }));
  text = text.replace(/(^|[^\\$])\$([^\s$](?:[^$\n]*?[^\s\\$])?)\$/g,
                      (m, pre, tex) => pre + keep({ tex: tex, display: false }));

  // 3. コードを元に戻してから Markdown を変換
  text = text.replace(/@@PH(\d+)@@/g, (m, i) => (store[i].raw !== undefined ? store[i].raw : m));
  let out = marked.parse(text, { gfm: true, breaks: true });

  // 4. 数式を KaTeX で描画して差し込む
  out = out.replace(/@@PH(\d+)@@/g, (m, i) => {
    const item = store[i];
    if (item.tex === undefined) return m;
    try {
      return katex.renderToString(item.tex, { displayMode: item.display, throwOnError: true, strict: "ignore" });
    } catch (e) {
      return '<span class="math-error">' + e.message.replace(/</g, "&lt;") + "</span>";
    }
  });

  const el = document.getElementById("content");
  el.innerHTML = out;

  // 5. Mermaid の図
  el.querySelectorAll("pre > code.language-mermaid").forEach((code) => {
    const div = document.createElement("div");
    div.className = "mermaid";
    div.textContent = code.textContent;
    code.parentElement.replaceWith(div);
  });
  mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
  mermaid.run({ querySelector: ".mermaid" });
})();
</script>
__WATCH__
</body>
</html>
"""

# --watch のときだけ埋め込む: ファイルが更新されたらスクロール位置を保ったまま再読み込み
WATCH_SCRIPT = r"""<script>
(function () {
  const key = "scroll:" + location.pathname;
  const y = sessionStorage.getItem(key);
  if (y !== null) window.addEventListener("load", () => setTimeout(() => window.scrollTo(0, +y), 300));
  let last = null;
  setInterval(async () => {
    try {
      const r = await fetch("/__mtime?f=" + encodeURIComponent(location.pathname), { cache: "no-store" });
      const t = await r.text();
      if (last !== null && t !== last) { sessionStorage.setItem(key, window.scrollY); location.reload(); }
      last = t;
    } catch (e) {}
  }, 1000);
})();
</script>"""


def convert(md_path: Path, watch: bool) -> Path:
    text = md_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    source = json.dumps(text, ensure_ascii=False).replace("</", "<\\/")
    page = (
        TEMPLATE.replace("__TITLE__", html.escape(md_path.stem))
        .replace("__SOURCE__", source)
        .replace("__WATCH__", WATCH_SCRIPT if watch else "")
    )
    out = md_path.with_suffix(".html")
    out.write_text(page, encoding="utf-8")
    return out


def target_files(names):
    if names:
        return [Path(n).resolve() for n in names]
    return sorted(p for p in ROOT.glob("*.md") if p.name not in EXCLUDE)


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/__mtime":
            name = parse_qs(url.query).get("f", [""])[0].lstrip("/")
            f = (ROOT / name).resolve()
            body = str(f.stat().st_mtime if f.is_file() and ROOT in f.parents else 0).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser(description="Markdown を数式・Mermaid 付きの HTML に変換")
    ap.add_argument("files", nargs="*", help="変換する .md（省略時はフォルダ内の全 .md）")
    ap.add_argument("--watch", action="store_true", help="保存のたびに自動変換・ブラウザ自動更新")
    ap.add_argument("--no-open", action="store_true", help="ブラウザを開かない")
    args = ap.parse_args()

    files = target_files(args.files)
    if not files:
        print("変換する .md がありません")
        return

    for f in files:
        print("変換:", convert(f, args.watch).name)

    if not args.watch:
        if not args.no_open:
            for f in files:
                webbrowser.open(f.with_suffix(".html").as_uri())
        return

    # --watch: ローカルサーバーで配信し、.md の変更を監視
    server = ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if not args.no_open:
        for f in files:
            rel = f.with_suffix(".html").relative_to(ROOT).as_posix()
            webbrowser.open(f"http://127.0.0.1:{PORT}/{rel}")
    print(f"監視中（http://127.0.0.1:{PORT}/）… Ctrl+C で終了")

    mtimes = {f: f.stat().st_mtime for f in files}
    try:
        while True:
            time.sleep(0.5)
            for f in target_files(args.files):
                m = f.stat().st_mtime
                if mtimes.get(f) != m:
                    mtimes[f] = m
                    print("更新:", convert(f, True).name)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
