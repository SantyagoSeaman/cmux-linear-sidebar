# How the Linear sidebar works

This page is for people who want to change the sidebar or understand why it is
built the way it is. To install and use it, read the [README](../README.md).

## The pieces

```
 cmux window
 ├── workspaces (left sidebar)          the one you select drives everything
 └── Dock (right sidebar), one pane
     ├── tab "Linear"         browser   http://127.0.0.1:47801/   web/
     └── tab "Linear server"  terminal  cmux-linear-sidebar       linear_sidebar/
                                          │
                                          ├── cmux workspace list --json   which workspace is selected
                                          ├── git -C <folder> …            branch and origin remote
                                          └── https://api.linear.app/graphql
```

The server is a single Python process using only the standard library
(Python 3.9+, the version macOS ships with the command line tools). The page
polls it; it never talks to Linear or cmux itself.

| Path | Role |
| --- | --- |
| `cmux-linear-sidebar` | Executable entry point; `--port` and `--refresh-seconds` override the environment, `--check-key` validates a key read from stdin |
| `linear_sidebar/config.py` | Settings from options and environment variables; the API key from the environment or the Keychain |
| `linear_sidebar/workspace.py` | The selected cmux workspace, its git branch and GitHub repository |
| `linear_sidebar/linear.py` | Linear GraphQL client and the queries |
| `linear_sidebar/view.py` | Pure functions that turn Linear data into the JSON the page renders |
| `linear_sidebar/service.py` | The poller: when to ask cmux and Linear, caching, error handling |
| `linear_sidebar/server.py` | HTTP server: static files, `/api/state`, `/api/open`, `/api/refresh` |
| `web/` | The page: plain HTML, CSS and JavaScript, no build step |
| `install.sh`, `uninstall.sh` | User-facing setup and removal |
| `scripts/dock-setup.sh` | Creates or repairs the two Dock tabs; safe to run again; `--restart` restarts the server |
| `scripts/dock_surfaces.py` | Finds those tabs in `cmux tree --json` and the server tab's resume binding |
| `scripts/common.sh` | Shell functions the three scripts share: finding the tabs, recognizing and stopping the server |

## From workspace to issues

For the selected workspace (`linear_sidebar/service.py`):

1. **Current issue.** If the folder is on a branch, Linear's
   `issueVcsBranchSearch(branchName:)` finds the issue whose ID appears in the
   branch name; Linear does the matching, so custom branch formats work as long
   as they contain the ID. Otherwise, a workspace titled exactly like an issue
   ID (`ABC-123`) is looked up with `issue(id:)`.
2. **Project.** The current issue's project. Without one: a Linear project with
   a resource link (`Project.externalLinks`) into the folder's GitHub `origin`
   repository; any URL inside the repository counts, such as a link to a file.
   When several projects link the repository, an active one wins over a
   finished one. Failing that, the project of an issue that has the branch's
   pull request attached (`attachmentsForURL`), using the pull request URLs cmux
   shows in its sidebar. The project list is read up to 500 projects with up
   to 50 resource links each; when Linear has more and nothing matched, the
   page says that the search stopped early.
3. **Card.** The current issue, its parent, and up to 250 sub-issues: the open
   ones listed, all of them counted in the progress bar. With more sub-issues
   than that, the counts get a `+`.
4. **Board.** The project's issues in started and unstarted states, plus those
   completed or canceled in the last 24 hours (`completedAt`/`canceledAt` with
   the relative date `-P1D`), up to 250; with more, the issue count gets a `+`.
   `view.board` groups open issues by workflow state (started before
   unstarted, then by the state's position) and puts the closed ones in one
   collapsed group. States of different teams with the same type and name form
   one group, as on Linear's project board. The current issue and the open
   sub-issues on its card are left out; sub-issues closed in the last 24 hours
   stay in the closed group.

Status icons follow Linear: a started state's pie is half full for a team's
first started state and three quarters for its last (`view.started_fills`),
using each team's workflow states, fetched once an hour.

## When it asks cmux and Linear

- The page requests `/api/state` every 1.5 seconds and re-renders only when the
  response changes.
- The poller thread runs only while the page has asked within the last 10
  seconds. Each tick it asks cmux for the selected workspace and git for its
  branch and remote.
- The Linear data depends on the workspace's *context*: its id, folder, branch,
  repository, and the issue ID in its title if the title is one. cmux renames
  workspaces by itself (after the running agent's conversation, for example);
  such a rename only updates the header. A new context shows its cached view at
  once and is reloaded when older than `LINEAR_SIDEBAR_REFRESH_SECONDS` (60 by
  default). Up to 50 views are cached. The project list is cached for 10
  minutes, workflow states for an hour. The refresh button reloads the view
  and also fetches the project list and workflow states again, so a project
  link added a moment ago is found.
- Linear can take seconds to answer. After a load the poller asks cmux again
  and shows the result only if the same workspace is still selected.
- When Linear fails, the last good view stays on screen with an error banner;
  without one, the page explains the error. Failures are retried after 15
  seconds. An unexpected exception while loading (an answer of an odd shape)
  is handled the same way, and printed in the Linear server tab.
- When cmux or git does not answer, the view on screen stays with a banner,
  and the next good poll removes it. A git that times out is not taken for a
  folder without a repository.
- The poller, not the HTTP server, reads the API key: reading the Keychain
  can wait on a macOS unlock prompt, and meanwhile the page says that it is
  reading the Keychain. A missing key is looked for again at every reload, so
  a key stored later is picked up. A Keychain that is locked or refuses access
  is a separate state, and it is not asked again until the user clicks
  **Try again**, so the unlock prompt does not keep coming back. Since the
  server keeps the key it read, `install.sh` restarts it after saving a new
  one (`dock-setup.sh --restart`).

## The HTTP API

| Request | Answer |
| --- | --- |
| `GET /`, `/app.js`, `/style.css` | The page |
| `GET /api/state` | The current view as JSON (see `view.state` for the keys), with `"app": "cmux-linear-sidebar"` |
| `POST /api/open` with `{"url": …}` | Opens an `https://linear.app` URL in the system browser |
| `POST /api/refresh` | Reloads the Linear data now |

Protections:

- The server binds to `127.0.0.1` and answers only requests whose `Host` header
  is `127.0.0.1:<port>` or `localhost:<port>`, so a web page that rebinds its
  domain to 127.0.0.1 cannot read your issues.
- The two `POST` endpoints need `Content-Type: application/json`, which other
  sites cannot send without a CORS preflight that this server never approves,
  and they refuse a foreign `Origin` or `Sec-Fetch-Site: cross-site`. A request
  without `Origin`, such as `curl`, is allowed.
- `/api/open` accepts only `https://linear.app` links without a user name or a
  port, and it calls `/usr/bin/open` without a shell.
- The page has a Content Security Policy that allows only its own scripts and
  styles, and it puts Linear text on screen as text, never as HTML. Every
  response forbids framing (`frame-ancestors 'none'`, which works only as a
  header) and content sniffing.
- The API key goes to Linear as an unredirected header, so a redirect from
  `api.linear.app` cannot carry it to another host.
- The scripts stop a process on the port only when its command line runs
  `cmux-linear-sidebar` and `/api/state` answers with the `app` name above.

## cmux behaviour this relies on

These are properties of cmux 0.65.0 that shaped the design. Re-check them when
cmux changes.

- **Socket access.** By default (`automation.socketControlMode: cmuxOnly`)
  cmux answers CLI calls only from processes started inside its terminals.
  That is why the server runs in a Dock terminal and not as a launchd service.
- **Why not a cmux sidebar extension or custom sidebar.** ExtensionKit sidebar
  extensions can only replace the left workspace list. Custom (interpreted)
  sidebars have no network access and receive outside data only through a few
  workspace fields, which the native workspace list shows as well.
- **`open` inside cmux terminals** is a wrapper
  (`cmux.app/Contents/Resources/bin/open`) that sends web URLs to cmux's own
  browser. The server calls `/usr/bin/open` to reach the system browser.
- **Dock tabs and the CLI.** `new-pane`/`new-surface --placement dock`,
  `close-surface`, `send` and `browser <surface> …` work on Dock tabs, but
  `move-surface`, `rename-tab` and `surface open --pane` do not find them. The
  server therefore names its tab with an OSC title sequence, and the scripts
  create and close tabs rather than moving them.
- **Recognizing the server tab.** `scripts/dock_surfaces.py` takes a terminal
  next to the page for the server's only when it is titled "Linear server"
  (the server sets that title while it runs) or its resume binding
  (`cmux surface resume show --json`) starts `cmux-linear-sidebar`, which
  survives the server stopping. Right after `dock-setup.sh` opens the tab
  neither is set yet, so it passes the terminals that were there before and
  takes the one new terminal. A terminal of your own in the same pane is never
  typed into or closed, whatever its title.
- **`new-surface --command`** types its text into the new terminal's shell, so
  `dock-setup.sh` quotes the program path.
- **Closing a Dock terminal** with `close-surface` fails while a program runs
  in it, so `uninstall.sh` stops the server first.
- **A page opened before the server listens** shows the browser's own error
  page, which never retries; `dock-setup.sh` reloads the page once the server
  answers. After that, `web/app.js` reconnects on its own.
- **Restart after a cmux relaunch.** cmux restores the Dock layout but not
  processes. A resume command set from the CLI (`cmux surface resume set`) is
  kept for manual restore only; it runs automatically after the user approves
  it once from the terminal's Resume Commands menu, and approvals are signed
  by cmux and stored per command prefix. `dock-setup.sh` puts settings other
  than the defaults into the command as `--port` and `--refresh-seconds`, and
  leaves an unchanged binding alone so that it keeps its approval. The API key
  never goes into the command.
- **A chromeless page** (no browser toolbar) is possible only for a browser
  control seeded from `~/.config/cmux/dock.json` with `"chrome": false`; the
  CLI cannot create one.

## Design decisions

The decisions behind this design, the alternatives that were tried and dropped (among
them a cmux ExtensionKit sidebar extension and a custom interpreted sidebar), and the cmux
and Linear behaviour that was learned the hard way are recorded in `.sidegraph/`, one JSON
file per record, anchored to the code they are about. Read them with
[Sidegraph](https://github.com/SantyagoSeaman/sidegraph), or as plain JSON. Build the code
graph they are anchored to with `graphify update .`; `graphify-out/` is not committed.

## Tests

```sh
python3 -m unittest discover -s tests -t .
/usr/bin/python3 -m unittest discover -s tests -t .   # the oldest supported Python
```

| Test file | Covers |
| --- | --- |
| `tests/test_view.py` | Turning Linear data into the view: groups, sorting, the card, project choice, colours |
| `tests/test_service.py` | The poller with a fake cmux and a fake Linear: caching, refresh, errors, renames |
| `tests/test_linear.py` | The GraphQL client against canned HTTP answers, including broken ones |
| `tests/test_server.py` | The HTTP server on a random port: host and origin checks, `/api/open` validation |
| `tests/test_workspace.py` | git in temporary folders and a fake `cmux` executable |
| `tests/test_config.py` | Settings, and the Keychain's answers: a key, no key, locked, no answer |
| `tests/test_cli.py` | Command line options |
| `tests/test_dock_surfaces.py` | Finding the page and server tabs in `cmux tree --json` |
| `tests/test_scripts.py` | `dock-setup.sh` and `common.sh` under bash with fake `cmux`, `curl`, `lsof` and `ps` |

Nothing in the test suite talks to the real cmux or Linear.
