'use strict';

const POLL_MS = 1500;
const COLLAPSED_KEY = 'linear-sidebar.collapsed';
const VIA = { branch: 'from branch', title: 'from workspace name' };

const ICONS = {
  refresh: '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11.5 7a4.5 4.5 0 1 1-1.32-3.18"/><path d="M11.5 2.2v2.9H8.6"/></svg>',
  branch: '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><circle cx="3.5" cy="2.5" r="1.3"/><circle cx="3.5" cy="9.5" r="1.3"/><circle cx="8.5" cy="4" r="1.3"/><path d="M3.5 3.8v4.4M8.5 5.3c0 2.2-5 1.2-5 2.9"/></svg>',
  chevron: '<svg class="chevron" width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 3.5l3 3 3-3"/></svg>',
  link: '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" aria-hidden="true"><path d="M5 7a2.4 2.4 0 0 0 3.4 0l1.7-1.7a2.4 2.4 0 0 0-3.4-3.4L6 2.6"/><path d="M7 5a2.4 2.4 0 0 0-3.4 0L1.9 6.7a2.4 2.4 0 0 0 3.4 3.4L6 9.4"/></svg>',
  unlinked: '<svg width="28" height="28" viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M11.5 16.5a5 5 0 0 0 7 0l3.5-3.5a5 5 0 0 0-7-7L14 7"/><path d="M16.5 11.5a5 5 0 0 0-7 0L6 15a5 5 0 0 0 7 7l1-1"/><path d="M4 4l20 20"/></svg>',
};

const app = document.getElementById('app');
let lastPayload = '';
let lastView = null;
let offline = false;
let collapsed = readCollapsed();

// ---- DOM helpers ---------------------------------------------------------

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function svg(name) {
  const template = document.createElement('template');
  template.innerHTML = ICONS[name]; // constant markup, never data
  return template.content.firstChild;
}

// Links keep their href for accessibility, but every click (also with a modifier or
// the middle button) goes to the system browser instead of navigating this page.
function link(url, props, ...children) {
  return h('a', {
    ...props,
    href: url,
    rel: 'noopener noreferrer',
    onclick: (event) => { event.preventDefault(); openUrl(url); },
    onauxclick: (event) => {
      if (event.button !== 1) return;
      event.preventDefault();
      openUrl(url);
    },
  }, ...children);
}

function statusIcon(icon) {
  const el = h('span', { class: `status ${icon.shape}`, 'aria-hidden': 'true' });
  el.style.setProperty('--c', icon.color);
  if (icon.shape === 'started') el.style.setProperty('--p', `${Math.round(icon.fill * 100)}%`);
  return el;
}

// ---- Collapsed sections, remembered per browser ---------------------------

function readCollapsed() {
  try { return JSON.parse(localStorage.getItem(COLLAPSED_KEY)) || {}; } catch { return {}; }
}

function isCollapsed(key, fallback) {
  return key in collapsed ? collapsed[key] : fallback;
}

function setCollapsed(key, value) {
  collapsed[key] = value;
  try { localStorage.setItem(COLLAPSED_KEY, JSON.stringify(collapsed)); } catch { /* private mode */ }
  if (lastView) render(lastView);
}

function toggle(key, closed, ...label) {
  return h('button', {
    type: 'button',
    class: 'toggle',
    'aria-expanded': String(!closed),
    onclick: () => setCollapsed(key, !closed),
  }, svg('chevron'), label);
}

// ---- Rendering -------------------------------------------------------------

function issueRow(issue) {
  return link(issue.url, { class: 'row', title: `${issue.id} · ${issue.state} — ${issue.title}` },
    statusIcon(issue.icon),
    h('span', { class: 'mono id' }, issue.id),
    h('span', { class: 'ellipsis' }, issue.title));
}

function header(view) {
  const ws = view.workspace;
  let branch = null;
  if (ws) {
    const label = ws.branch || (ws.isRepo ? 'detached HEAD' : 'not a git repository');
    branch = h('div', { class: 'branch' }, svg('branch'), h('span', {}, label));
  }
  return h('header', { class: 'header' },
    h('div', { class: 'title-row' },
      h('h1', { class: 'title' }, ws ? ws.title : 'Linear'),
      h('button', { type: 'button', class: 'icon-button', 'aria-label': 'Refresh', title: 'Refresh', onclick: refresh }, svg('refresh'))),
    branch);
}

function currentCard(card) {
  const subsClosed = isCollapsed('sub-issues', false);
  // Linear had more sub-issues than one page: the counts are a lower bound.
  const more = card.progress && card.progress.more ? '+' : '';
  return h('section', { class: 'card', 'aria-label': 'Current issue' },
    card.parent && link(card.parent.url, { class: 'crumb', title: card.parent.title },
      h('span', { class: 'mono' }, card.parent.id), h('span', {}, '›'), h('span', { class: 'ellipsis' }, card.parent.title)),
    link(card.url, { class: 'card-main' },
      h('div', { class: 'card-meta' },
        statusIcon(card.icon), h('span', { class: 'mono strong' }, card.id), h('span', {}, card.state),
        h('span', { class: 'spacer' }), h('span', {}, VIA[card.via] || '')),
      h('div', { class: 'card-title' }, card.title)),
    card.progress && progress(card.progress),
    card.subIssues.length > 0 && h('div', { class: 'subs' },
      toggle('sub-issues', subsClosed, h('span', { class: 'toggle-name' }, 'Sub-issues'), h('span', {}, `${card.subIssues.length}${more} open`)),
      !subsClosed && card.subIssues.map(issueRow)));
}

function progress({ done, total, more }) {
  const of = `${done} of ${total}${more ? '+' : ''}`;
  const bar = h('div', { class: 'bar', role: 'img', 'aria-label': `${of} sub-issues done` },
    h('div', { class: 'bar-fill' }));
  bar.firstChild.style.width = `${Math.round((done / total) * 100)}%`;
  return h('div', { class: 'progress' }, bar, h('span', { class: 'muted' }, `${of} done`));
}

// A group with `more` lists only part of its issues; the link opens the rest in Linear.
function group(g) {
  const key = `group:${g.key}`;
  const closed = isCollapsed(key, g.collapsed);
  return h('div', { class: 'group' },
    toggle(key, closed, statusIcon(g.icon), h('span', { class: 'toggle-name' }, g.name),
      h('span', { class: 'muted' }, `${g.count}${g.countMore ? '+' : ''}`)),
    !closed && g.issues.map(issueRow),
    !closed && g.more && link(g.more, { class: 'row more', title: 'The project’s issues in Linear' }, 'More'));
}

function board(view) {
  const project = view.project;
  if (!project) {
    return h('p', { class: 'muted pad' }, `${view.current.id} is not in a project.`);
  }
  // `+`: Linear had more than one page, so the count is a lower bound. A server started
  // before the counts existed sends none.
  const counts = [];
  if (project.open != null) counts.push(`${project.open}${project.openMore ? '+' : ''} open`);
  if (project.backlog > 0) counts.push(`${project.backlog}${project.backlogMore ? '+' : ''} backlog`);
  const via = {
    repo: view.workspace.repo && `linked by github.com/${view.workspace.repo}`,
    'pull-request': 'found through this branch’s pull request',
  }[project.via];
  return [
    h('div', { class: 'project' },
      h('span', {}, 'Project'),
      link(project.url, {}, project.name),
      h('span', { class: 'spacer' }),
      h('span', {}, counts.join(' · '))),
    via && h('div', { class: 'via' }, svg('link'), via),
    view.groups.map(group),
    view.groups.length === 0 && h('p', { class: 'muted pad' }, 'Nothing in progress, to do, or closed in the last 24 hours.'),
    view.backlog && group(view.backlog),
  ];
}

function emptyState(title, paragraphs, steps, action) {
  return h('div', { class: 'empty' },
    h('div', { class: 'muted' }, svg('unlinked')),
    h('h2', { class: 'empty-title' }, title),
    paragraphs.map((text) => h('p', {}, text)),
    steps && steps.length > 0 && [
      h('div', { class: 'steps-label' }, 'To connect it'),
      h('ol', { class: 'steps' }, steps.map((text, i) => h('li', {}, h('span', { class: 'muted' }, `${i + 1}  `), text))),
    ],
    action);
}

function noProject(view) {
  const ws = view.workspace;
  const reasons = [];
  if (!ws.isRepo) {
    reasons.push('This folder is not a git repository.');
  } else {
    reasons.push(ws.branch ? `No Linear issue matches the branch ${ws.branch}.` : 'The repository is on a detached HEAD.');
    reasons.push(ws.repo ? `No Linear project links to github.com/${ws.repo}.` : 'The repository has no GitHub remote.');
  }
  if (view.message) reasons.push(view.message);
  const steps = ws.isRepo ? [
    'Work on a branch named after an issue, like abc-123-fix-login.',
    ws.repo
      ? `Or add https://github.com/${ws.repo} to the project’s Resources in Linear.`
      : 'Or push the repository to GitHub and add its URL to the project’s Resources in Linear.',
  ] : [];
  return emptyState(`No Linear project for ${ws.title}`, reasons, steps,
    link('https://linear.app', { class: 'button' }, 'Open Linear'));
}

function content(view) {
  const box = h('div', { class: 'content' });
  if (view.error) box.append(h('div', { class: 'banner', role: 'status' }, `Couldn’t refresh: ${view.error}`));
  switch (view.status) {
    case 'loading':
      box.append(h('p', { class: 'muted pad' }, view.message || 'Loading…'));
      break;
    case 'no-key':
      // The installer asks for the key without echoing it; a command line would keep it in the history.
      box.append(emptyState('No Linear API key', [
        'To store one in your Keychain, run this in a cmux terminal:',
        h('code', { class: 'command' }, view.message || 'install.sh --new-key'),
      ]));
      break;
    case 'key-error':
      box.append(emptyState('Can’t read the Linear API key', [
        view.message || '',
        'Unlock the login keychain if macOS asks for it, then try again.',
      ], null, h('button', { type: 'button', class: 'button', onclick: refresh }, 'Try again')));
      break;
    case 'no-workspace':
      box.append(h('p', { class: 'muted pad' }, 'No workspace is selected.'));
      break;
    case 'cmux-error':
      box.append(emptyState('Can’t read the workspace', [view.message || '']));
      break;
    case 'error':
      box.append(emptyState('Can’t load issues from Linear', [
        view.message || '',
        'The sidebar tries again in a few seconds.',
      ]));
      break;
    case 'no-project':
      box.append(noProject(view));
      break;
    default:
      if (view.current) box.append(currentCard(view.current));
      box.append(...[board(view)].flat(Infinity).filter(Boolean));
  }
  return box;
}

function updatedText(view) {
  if (!view.updatedAt) return '';
  const seconds = Math.max(0, Math.round((Date.now() - view.updatedAt) / 1000));
  if (seconds < 10) return 'Updated just now';
  if (seconds < 60) return `Updated ${seconds}s ago`;
  return `Updated ${Math.round(seconds / 60)} min ago`;
}

function footer(view) {
  return h('footer', { class: 'footer' },
    h('span', { class: 'updated spacer' }, updatedText(view)),
    link('https://linear.app', {}, 'Open Linear'));
}

function render(view) {
  const previous = app.querySelector('.content');
  const scrollTop = previous ? previous.scrollTop : 0;
  app.replaceChildren(header(view), content(view), footer(view));
  app.querySelector('.content').scrollTop = scrollTop;
}

function renderOffline() {
  app.replaceChildren(
    header({ workspace: null }),
    h('div', { class: 'content' }, emptyState('The sidebar server is not running', [
      'Start cmux-linear-sidebar in the Dock’s “Linear server” tab, or run scripts/dock-setup.sh. This page reconnects on its own.',
    ])),
    footer({}));
}

// ---- Server calls ----------------------------------------------------------

async function poll() {
  try {
    const response = await fetch('/api/state', { cache: 'no-store' });
    const text = await response.text();
    if (offline || text !== lastPayload) {
      offline = false;
      lastPayload = text;
      lastView = JSON.parse(text);
      render(lastView);
    }
  } catch {
    if (!offline) {
      offline = true;
      lastPayload = '';
      renderOffline();
    }
  }
  setTimeout(poll, POLL_MS);
}

// The server accepts actions only as same-origin JSON requests.
function post(path, body) {
  fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).catch(() => {});
}

function openUrl(url) {
  post('/api/open', { url });
}

function refresh() {
  post('/api/refresh', {});
}

setInterval(() => {
  const el = app.querySelector('.updated');
  if (el && lastView) el.textContent = updatedText(lastView);
}, 5000);

poll();
