# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately with GitHub's **Report a vulnerability** button
(Security tab) on this repository. Do not open a public issue for security problems. You
should get a first response within a few days.

## What the sidebar touches

The sidebar is a local program for one Mac user. Its whole data surface:

- **Linear:** it sends read-only GraphQL queries to `https://api.linear.app/graphql` with
  your personal API key, which is never forwarded if that address redirects elsewhere. It
  never changes anything in Linear.
- **API key:** `install.sh` stores the key in your login Keychain as the generic password
  `cmux-linear-sidebar`, passing it to `security` on standard input so it never shows up in
  a process list. The program reads it from there, or from the `LINEAR_API_KEY`
  environment variable. The key is not written to any file or log, and the scripts never
  put it on a command line.
- **cmux:** it runs the cmux command line tool to learn which workspace is selected
  (`workspace list`, `sidebar-state`) and, from the scripts, to open, find and close its
  own two Dock tabs.
- **git:** it reads the selected folder's current branch and `origin` remote. It never
  writes to a repository.
- **Local server:** it listens on `127.0.0.1:47801` (or the port you set) only. It answers
  requests whose `Host` is `127.0.0.1` or `localhost` on that port, takes actions only as
  same-origin JSON requests from its own page, refuses to be framed by other pages, and
  opens nothing but `https://linear.app` links, through `/usr/bin/open`.
- **Files:** it writes no files. `install.sh` and `uninstall.sh` change only the Keychain
  item above and the Dock tabs. They stop a process on the port only after both its
  command line and its answer show it is this sidebar's server.

## Supported versions

Only the latest commit on `main` is supported.
