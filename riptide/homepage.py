"""Landing page for riptide.codeovertcp.com — served at GET /.

Self-contained single-file HTML (no external assets, no CDNs). Live stats are
fetched client-side from the same-origin /health endpoint; the page degrades
to static content if that fails. Keep it dependency-free: this file must stay
readable as documentation of the bot itself.
"""

HOME_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Riptide — autonomous PR review &amp; fix bot</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #0d1117; color: #c9d1d9;
    font: 16px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 24px;
  }
  main { max-width: 720px; width: 100%; }
  h1 { color: #58a6ff; margin: 0 0 4px; font-size: 1.7em; }
  h1 .bot { color: #3fb950; }
  .tag { color: #8b949e; margin: 0 0 28px; }
  .card {
    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    padding: 18px 20px; margin: 14px 0;
  }
  .card h2 { margin: 0 0 10px; font-size: 0.85em; text-transform: uppercase;
             letter-spacing: 0.08em; color: #8b949e; }
  .stats { display: flex; gap: 28px; flex-wrap: wrap; }
  .stat b { display: block; font-size: 1.9em; color: #58a6ff; }
  .stat span { color: #8b949e; font-size: 0.85em; }
  .live::before {
    content: ""; display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: #3fb950; margin-right: 6px; vertical-align: 1px;
  }
  .stale::before { background: #d29922; }
  ol, ul { margin: 0; padding-left: 1.2em; }
  li { margin: 8px 0; }
  code { background: #0d1117; border: 1px solid #30363d; border-radius: 5px;
         padding: 1px 6px; font-size: 0.9em; color: #79c0ff; }
  .pipe { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
  .stage { background: #21262d; border: 1px solid #30363d; border-radius: 6px;
           padding: 4px 10px; font-size: 0.85em; }
  .arrow { color: #3fb950; }
  a { color: #58a6ff; text-decoration: none; }
  a:hover { text-decoration: underline; }
  footer { margin-top: 26px; color: #8b949e; font-size: 0.85em; }
</style>
</head>
<body>
<main>
  <h1>Riptide <span class="bot">🤖</span></h1>
  <p class="tag">Autonomous PR review &amp; fix pipeline for <code>ChonSong/riptide</code> — deterministic scans, LLM judgment, CI-gated verdicts.</p>

  <div class="card">
    <h2>Live status</h2>
    <div class="stats" id="stats">
      <div class="stat"><b id="s-sess">–</b><span>tracked sessions</span></div>
      <div class="stat"><b id="s-runs">–</b><span>active runs</span></div>
      <div class="stat"><b id="s-phase">–</b><span>current phase</span></div>
    </div>
    <p id="health-note" style="margin:10px 0 0; color:#8b949e; font-size:.85em;">checking /health …</p>
  </div>

  <div class="card">
    <h2>How a review happens</h2>
    <ol>
      <li>Trigger — comment <code>@riptide-bot review</code> / <code>@riptide-bot fix</code>, or the 15-min poller finds a stale PR.</li>
      <li>Spawn — a Hermes session starts with the Conductor track <code>riptide-review-&lt;owner&gt;-&lt;repo&gt;-&lt;pr&gt;</code>.</li>
    </ol>
    <div class="pipe">
      <span class="stage">Probe</span><span class="arrow">→</span>
      <span class="stage">Judge</span><span class="arrow">→</span>
      <span class="stage">Artisan</span><span class="arrow">→</span>
      <span class="stage">Engine</span><span class="arrow">→</span>
      <span class="stage">ci_verifier</span><span class="arrow">→</span>
      <span class="stage">Scribe</span><span class="arrow">→</span>
      <span class="stage">PR comment</span>
    </div>
    <p style="margin:10px 0 0; color:#8b949e; font-size:.9em;">
      The Scribe's verdict clears the <code>Riptide Review Required</code> CI gate only when
      a later commit touches the files the findings name. Fix sessions read findings from
      <em>all</em> reviewers, verify each against the code, and push with tests.
    </p>
  </div>

  <div class="card">
    <h2>Specs</h2>
    <ul>
      <li><a href="https://github.com/ChonSong/riptide/blob/main/docs/REVIEW-CONTRACT.md">Review Contract</a> — comment markers, CI gate mechanics</li>
      <li><a href="https://github.com/ChonSong/riptide/blob/main/docs/DATA-AND-PROMPTS.md">Data &amp; Prompts</a> — findings schema, real prompt texts, linked specimens</li>
      <li><a href="https://github.com/ChonSong/riptide/blob/main/AGENTS.md">AGENTS.md</a> — architecture, layout, testing traps</li>
    </ul>
  </div>

  <footer>riptide webhook server · <a href="/health">/health</a> · <a href="/metrics">/metrics</a></footer>
</main>
<script>
fetch('/health').then(r => r.json()).then(h => {
  document.getElementById('s-sess').textContent = h.sessions ?? '–';
  document.getElementById('s-runs').textContent = (h.active_runs ?? h.active_streams ?? '–');
  const run = (h.runs && h.runs[0]) || {};
  document.getElementById('s-phase').textContent = run.phase || 'idle';
  const note = document.getElementById('health-note');
  note.textContent = 'live · model: ' + (run.model || h.model || 'n/a');
  note.classList.add('live');
}).catch(() => {
  const note = document.getElementById('health-note');
  note.textContent = 'health endpoint unreachable — stats unavailable';
  note.classList.add('stale');
});
</script>
</body>
</html>
"""
